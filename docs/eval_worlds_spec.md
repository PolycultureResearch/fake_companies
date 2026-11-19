# Spec: world sets for evaluation

**Status:** proposal, 2026-11-19. Phase 1 is ready to build.

## 1. Purpose

fake_companies generates one company per run: one config, one seed, one set of
anomalies. Consumers that want to *evaluate* an analytics tool need more than
one company. They need the same company with and without an anomaly, the same
company across many seeds, and the same company with one property changed.

This spec adds **world sets**: a batch of related generated companies with a
manifest that says how they relate, and ground truth that is measured from the
data instead of declared by hand.

The driving consumer is Breakdown's evaluation
(`knowledge/evaluation_spec.md` in the breakdown repo), which decides what is
built first. The features are general: paired runs, seed sweeps and measured
effects are what any detector or attribution tool needs to be scored. Tremor
keeps working unchanged, because everything here is additive.

## 2. Terms

- **World:** one generated company. Identified by vertical, config, seed,
  regime overrides, and the anomalies applied.
- **Null world:** a world with no anomalies of any kind.
- **Single world:** a world with exactly one anomaly.
- **Twin:** a single world and the null world of the same config, seed and
  regime.
- **World set:** a directory of worlds plus one manifest.
- **Regime:** a named set of overrides to the baseline shape (growth, history
  length, seasonality, noise). Baseline shape is still not an anomaly and still
  never enters ground truth.

## 3. What Breakdown's first two evaluations showed was missing

1. **No world without anomalies.** Calibration was run on the gaps between
   anomalies in a scripted scenario. Those gaps held anomaly aftermath,
   holidays and promotions.
2. **Declared effects were wrong.** `affected_metrics` comes from a hand-kept
   map from driver to metrics (`verticals/<name>/metrics.py`). For three of six
   Alpenglow anomalies the listed node did not move: two shifted category mix
   with total units unchanged, and one was about 3% of its node.
3. **Onset is the driver's date.** The Alpenglow recall is dated by order; the
   return rate moves about two weeks later. A consumer scoring detection at the
   declared date scores the wrong window.
4. **The root is named as a latent driver** (`spend.paid_social`). A consumer
   has to map that to a metric and a dimension value by hand.
5. **One seed per company.** A result could not be separated from the draw.
6. **Promotions and holidays are in the config, not in the data.** A consumer
   cannot treat them as known inputs or exclude them.
7. **The scenario trees are not evaluation trees.** The Alpenglow tree in
   `examples/` is nine flows with no rate and no stock.

## 4. Phase 1: world sets and measured truth

### 4.1 Anomaly selection on `generate`

New options on `fake-companies generate`, and the same as arguments to
`generate()`:

- `--anomalies all` (default, today's behaviour), `none`, or
  `only:<name>[,<name>...]`.
- `none` drops scripted and surprise anomalies. `only:` keeps the named
  scripted anomalies and drops surprise sampling.

Hard requirement: the default path is byte-identical to today. The golden
hashes do not change.

Selection must not disturb the random streams of anything else. Surprise
sampling has its own stream today; confirm that skipping it leaves every other
stream untouched, and say so in a test.

### 4.2 Regime overrides

A `--set key=value` option (repeatable) that overrides a config value by dotted
path after load and before validation, for example `--set timeline.days=1095`
or `--set calendar.annual_amp=0`. Overrides are recorded in the run manifest.

Named regimes are a small file of overrides per vertical
(`configs/regimes/<vertical>.yaml`), so a consumer can ask for `flat`,
`declining`, `accelerating`, `long_history` or `no_seasonality` without knowing
each vertical's driver names. Growth lives on each driver, so a regime has to
set it per driver; that is why regimes are per vertical.

A regime is a statement about the baseline. It must not add or remove
anomalies.

### 4.3 The `worlds` command

```
fake-companies worlds --config configs/alpenglow_retail_dtc.yaml \
    --seeds 0-9 --regime baseline --regime flat \
    --null --singles all --out out/worlds/alpenglow
```

For each regime and seed it writes:

- the null world, when `--null` is given
- one single world per selected anomaly (`--singles all` or a list of names)
- optionally the full scripted world (`--story`)

Worlds are written as today's DuckDB plus sidecars. `--format parquet` writes
the raw tables as Parquet instead, which is smaller and is what most consumers
read.

Generation of independent worlds may run in parallel (`--jobs`). Output must
not depend on the number of jobs.

### 4.4 World-set manifest

`<out>/worlds.json`, one entry per world:

```json
{
  "schema_version": 1,
  "generator_version": "…",
  "config": "configs/alpenglow_retail_dtc.yaml",
  "config_sha256": "…",
  "vertical": "retail_dtc",
  "worlds": [
    {
      "id": "alpenglow.baseline.s3.null",
      "seed": 3,
      "regime": "baseline",
      "overrides": {},
      "kind": "null",
      "anomalies": [],
      "twin_of": null,
      "path": "baseline/s3/null.duckdb",
      "ground_truth": "baseline/s3/null.ground_truth.json"
    },
    {
      "id": "alpenglow.baseline.s3.only.spring_budget_cut",
      "seed": 3,
      "regime": "baseline",
      "kind": "single",
      "anomalies": ["spring_budget_cut"],
      "twin_of": "alpenglow.baseline.s3.null",
      "path": "…",
      "ground_truth": "…",
      "effects": "baseline/s3/only.spring_budget_cut.effects.parquet"
    }
  ]
}
```

The manifest is the contract. A consumer reads worlds through it and nothing
else. World ids are stable: the same config, seed, regime and anomaly always
give the same id and the same bytes.

### 4.5 Measured effects

For each twin, a **measured effects** table: the day-by-day difference between
the single world and its null world, for every metric the vertical exposes.

- Computed from the raw tables with the same aggregation the dbt metrics use.
  Where running dbt and MetricFlow per world is too slow, compute the metric
  series directly from the frames with a per-vertical function, and test that
  it agrees with the MetricFlow series on a smoke config.
- Columns: `date`, `metric`, `dimension`, `value` (null for the total),
  `null_value`, `world_value`, `diff`.
- At least the total for every metric, and each value of the dimensions the
  anomaly's `segment` names.

From it, per anomaly and metric, a summary added to that world's ground truth
record (new fields; existing fields keep their names and meaning):

- `measured_effects`: for each metric, the relative change over the anomaly
  window, the first date the metric's difference exceeds its noise floor
  (`onset`), and the last (`offset`).
- `declared_affected_metrics_confirmed`: which of the declared
  `affected_metrics` moved, and which did not.

**Noise floor and coupling.** Two runs of one seed diverge in their draws once
an anomaly changes a rate, because the entity layer consumes a different number
of random values. So a twin difference is the causal effect plus realization
noise. Phase 1 must:

1. Measure that noise: the twin difference on days before the anomaly starts
   must be exactly zero for every metric (nothing has diverged yet). Assert it.
2. Report, per metric, the size of the difference on metrics the anomaly cannot
   reach (another branch of the business). That is the noise floor used for
   `onset`.
3. Provide the cross-seed mean and spread of each effect in a set-level
   summary (`effects_summary.parquet`), since the mean across seeds is the
   best estimate of the expected effect.

Tighter coupling (draws that stay aligned across the twin) would shrink the
noise. It likely means changing how entity draws consume the streams, which
would move the golden hashes. It is out of scope for phase 1; record what was
learned about where the streams diverge.

### 4.6 Root labels in consumer terms

Each ground truth record gains:

- `root_metric`: the MetricFlow metric closest to the driver (the first metric
  the anomaly moves).
- `root_dimension` and `root_value`: when the anomaly has a `segment`, the
  semantic-layer dimension and value it corresponds to; otherwise null.

These come from the vertical (`metrics.py` grows a driver-to-root map), and
the conformance test checks every driver has one. Where the measured effects
disagree with the declared root (the root metric does not move), generation
warns and the record says so.

### 4.7 Tests

- Default generation is byte-identical: golden hashes unchanged.
- `--anomalies none` yields an empty ground truth and a world whose metrics
  show no anomaly-sized excursions in the scripted windows.
- A single world equals the null world, byte for byte, in every raw table on
  all days before the anomaly's start (for rate anomalies), and in every table
  the anomaly cannot reach where streams are independent.
- World ids and bytes are stable across runs and across `--jobs` values.
- Measured effects: for each smoke-config anomaly, the root metric's measured
  relative change has the declared sign and at least the size the existing
  ground-truth honesty tests require.
- The manifest validates against a pydantic model that is exported for
  consumers.

Use the 90-day smoke configs. A world set of 3 seeds × null plus singles for
one smoke config should build in well under a minute.

### 4.8 Documentation

- `docs/using.md`: a recipe for building a world set and reading the manifest.
- `AGENTS.md`: world sets in "What this produces", and the rule that selection
  and overrides never change the default bytes.
- `docs/roadmap.md`: a pointer to this spec.

## 5. Phase 2: richer worlds

Ordered by what Breakdown's evaluation needs next. Each is its own change.

1. **Calendar table.** `meta.calendar`: one row per date and event, for
   holidays, promotions and any scripted baseline event, with the segment it
   applies to. Promotions are baseline shape, so they belong here and not in
   ground truth.
2. **Canonical evaluation trees.** One Breakdown tree per vertical under
   `examples/`, each with at least one rate, one stock, one node with a learned
   parent, one formula node and one sliceable dimension.
   `scripts/verify_consumers.py` checks every metric the tree binds exists.
3. **Anomaly shapes.**
   - *Off-tree:* a driver change whose `root_metric` is deliberately absent
     from the canonical tree. Ground truth marks it `off_tree: true`.
   - *Confounder:* one latent cause that moves two drivers with no path between
     them in the tree.
   - *Mix shift:* declared as such, with `root_metric` set to the share metric
     that does move, since the total does not.
   - *Magnitude ladder:* `--magnitudes 1.05,1.1,1.2,1.5` on `worlds`, which
     writes one single world per rung.
4. **Noise options.** Overdispersion on count draws, heavy-tailed order values,
   and late-arriving rows that restate recent days. Today's draws are Poisson
   and binomial, so a tool tuned here is tuned to those.
5. **Longer and shorter histories as first-class regimes,** including a
   company with under a year of data.
6. **Blind sets.** A `worlds --blind` layout that writes ground truth and
   effects to a separate directory, so an agent can be handed the data alone.

## 6. Not in scope

- Scoring tools for any consumer. Breakdown's metrics live in the breakdown
  repo; `fake-companies score` stays as it is.
- New verticals.
- Changing existing ground truth field names or meanings.
- Hosting or publishing world sets. They are built locally from a config and a
  seed range; the published demo bundles are unchanged.

## 7. Housekeeping found on the way

Not part of this work, listed so they are not lost:

- The working tree holds untracked duplicates with " 2" in the name under
  `configs/`, `dbt/`, `examples/`, `src/` and `tests/`. They look like
  file-sync copies. `tests/test_vertical_conformance 2.py` would be collected
  by pytest if it were ever committed.
- Breakdown's `dbt` provider reads the dbt project even when every series is
  already cached, so a consumer needs this repo checked out to read a
  snapshot. That is a Breakdown change, noted there.

---

*This document is written and maintained by an AI agent (Claude), with human oversight.*
