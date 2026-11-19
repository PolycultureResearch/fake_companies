# Spec: world sets for evaluation

**Status:** phase 1 built, 2026-11-19 (see §8 for what changed on the way). Phase 2 not started.

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

## 8. Decisions made while building phase 1

Where the spec was silent or turned out wrong, the smallest sensible choice
was made. Each is recorded here so the spec and the code agree.

**The twin is not identical before the anomaly (§4.5, §4.7).** The spec
asserted that a single world equals its null world on every day before the
anomaly starts. That holds for dq anomalies (only the target table changes,
and only inside the window) and for entity builders that draw in time order:
CPG scan sales and shipments, and retail session conversion. It does not hold
for the rest. Each builder draws for the whole timeline from one stream, and
several draw in an order that is not time: B2C and retail sessions and ad
spend go channel by channel, retail repeat orders customer by customer, the
B2B pipeline source by source, and the `_loaded_at` lags table by table. An
anomaly that changes how many values such a builder consumes re-deals rows
dated long before it. So:

- the test asserts what is true, per vertical: which tables match over the
  whole timeline, which match before the anomaly, and that the others differ
  (`tests/test_effects.py`, `TWIN_COUPLING`);
- a twin difference is treated as effect plus realization noise everywhere,
  and the noise is measured per series;
- nothing about how builders consume their streams was changed.

**The noise floor (§4.5).** The spec defined it from metrics the anomaly
cannot reach, which needs a reachability map — the hand-kept map this work
exists to check. Instead each series gets its own: the larger of the RMS twin
difference before the anomaly and the RMS day-over-day change of the
difference after the streams diverge (the two largest steps set aside: they
are the anomaly switching on and off), times `NOISE_K = 4`. `onset` and
`offset` are the first and last day over that floor, as specified, and
`first_diff` is the first day the twins differ at all, which is the exact
arrival date whenever the streams were aligned until the anomaly.

Whether a series *moved* is a separate, window-level test, because a per-day
threshold misses a real effect spread thinly over weeks (a jackets recall
adds two or three returned units a day) and fires on a stray lumpy day months
later. `z_window` is the mean difference over the declared window over its
standard error; `z_extended` is the same over the window stretched by its own
length, for effects that land late. The standard error is `sigma / sqrt(n)`,
or the spread of the mean over placebo windows elsewhere in the same twin
when that is smaller (re-dealt timing cancels inside a window). Stock-like
series, whose noise accumulates, are tested against the level noise expected
at the window. The constants are in `worlds.json` under `noise`. Per-twin
verdicts on stock-like series are the weakest part: trust the cross-seed
summary there.

**`not_detected`, not "did not move" (§4.5).**
`declared_affected_metrics_confirmed` has three lists: `moved`,
`moved_in_slice_only` (the total did not clear its noise but the metric at the
anomaly's own slice did, which is what a segmented anomaly is designed to do)
and `not_detected`. One twin cannot show that a metric did not move.
`effects_summary.parquet` adds `rel_change_sem`, `t_across_seeds` and
`moved_share` so the cross-seed answer can be read directly.

**Cohort-dated metrics.** A metric bucketed by the start of a cohort (B2C
trial metrics, by trial start) differs before the anomaly for causal reasons.
Its pre-onset noise estimate is too large, which errs towards `not_detected`.

**Metric series come from the dbt project, in-process (§4.5).** One `dbt
build` on the 90-day retail smoke config takes about 5 s and each `mf query`
2 to 3 s, so dbt per world was ruled out. Instead of a second, per-vertical
copy of the aggregation, `fake_companies.semantic` runs the vertical's own
staging and mart SQL in an in-memory DuckDB and evaluates its semantic YAML.
Every metric of all four verticals is reproduced exactly on the smoke configs
(`tests/test_semantic_matches_metricflow.py`), so nothing is left out of the
effects tables today; a metric the module cannot reproduce is refused by name
and listed in `worlds.json` under `unsupported_metrics`. Building effects
needs the repo's `dbt/` directory (not the dbt extra); `--no-effects` builds a
set without them.

**Root labels carry a slice, not one pair (§4.6).** The spec gave
`root_dimension` / `root_value` only to anomalies with a `segment`. But the
driver itself names a member (`spend.paid_social` is marketing spend *for
paid social*), and a segmented anomaly is then two dimensions at once. So the
root slice is the driver's member followed by the segment dims the entity
builder honors; `root_dimension` / `root_value` are comma-joined when there is
more than one (`channel,device` / `paid_search,mobile`), which is also how the
effects table spells that slice, and `root_slice` holds the mapping. A segment
on a driver whose builder ignores segments adds nothing to the slice, because
it does nothing to the data.

**The root is the closest metric MetricFlow can slice.** `return_rate.<cat>`
and `churn.<plan>` root at the numerator count (`returned_units`,
`churned_subscriptions`): the ratio's two sides sit on different semantic
models, so no single group-by addresses its category or plan.
`category_demand.<cat>` roots at `units` by category.

**Dimension names.** Effects tables and root labels use the dimension's name
on its semantic model (`channel`). `worlds.json` → `metrics[].dimensions` maps
each to the MetricFlow group-by id (`session__channel`), or null where the
metric spans models. Slices match by exact name: a metric whose model calls
the same concept something else (`signup_channel`, `first_channel`) appears in
totals only.

**Where the new ground-truth fields live.** In `ground_truth.json`.
`meta.ground_truth` keeps its twelve columns, because the table's bytes are
pinned by the golden hashes. The root label is written by every run; the
measured fields only for single worlds of a set.

**Manifest additions.** Beyond §4.4: `company_slug`, `format`, `seeds`,
`regimes`, `effects_summary`, `noise`, `metrics`, `unsupported_metrics`, and
per world `run_manifest` and `config_hash`. `config_sha256` is of the YAML
file's bytes; `config_hash` is of the effective config (regime, overrides and
selection applied).

**Singles imply the null world (§4.3).** A single world without its twin
cannot be measured, so `--singles` builds the null world whether or not
`--null` is given. The story world gets no effects table: it holds several
anomalies and a twin difference would not separate them.

**Bytes (§4.4).** Parquet worlds are byte-stable and tested as such. DuckDB
worlds are tested by content (Parquet export hashes); the database file is
not promised to be byte-identical.

**Overrides (§4.2).** `*` in a path matches every key or item at that point;
values are parsed as YAML; a path may end in a new key but not pass through a
missing section. `--set` on `worlds` applies to every world on top of the
regime. `long_history` extends the timeline forward to 1095 days so scripted
anomalies keep their dates. `no_seasonality` removes the annual cycle, the
holiday effect and the day-of-week shape; it leaves promotions alone.

**Growth regimes in `cpg_wholesale` are nearly empty.** Only brand-marketing
spend has a growth curve there, and marketing is decoupled from sales, so
`flat`, `declining` and `accelerating` change `marketing_spend` and nothing
else. A growth curve on consumer demand is a change to the vertical.

**Per-world metric series (added after phase 1, for Breakdown's runner).**
Every world gets `<world>.series.parquet`: `date`, `metric`, `dimension`,
`value`, `metric_value`, `numerator`, `denominator`, for the total of every
metric and every single-dimension slice listed in `metrics[].dimensions`.
Nothing was cut: on Alpenglow the file is a few MB per world. `numerator` and
`denominator` are filled for ratio metrics so a ratio can be re-aggregated as
a ratio of sums. `metrics[]` gained `time_aggregation`, `inputs` and `expr`.
The semantic YAML cannot say that a metric is a stock (MRR is a `sum` over a
subscription-day model), so each vertical names its stocks
(`STOCK_METRICS` in `metrics.py`). `--no-raw` skips the raw tables; a world's
`path` is then null.

**Declared `affected_metrics` corrected where they were wrong by
construction.** Phase 1 left the hand-kept map alone and recorded the
disagreements. Devon then decided the labels should be right, accepting that
default `meta.ground_truth` output changes:

- retail `category_demand.*`: `units` removed (a mix shift cannot move total
  units; measured exactly zero), `margin_rate` added. Declared now:
  `gross_revenue`, `net_revenue`, `gross_margin`, `aov`, `margin_rate`. Units
  by category stays the root slice.
- B2B `cycle_time_scale`: `proposals_sent`, `negotiations`, `deals_won`,
  `bookings`, `invoiced_revenue` added.
- Nothing else was removed. Every (driver, declared metric) pair of every
  vertical was probed on the smoke configs for an exactly-zero twin
  difference; the only other zeros are B2C drivers whose base rate or
  elasticity is zero in the smoke config, which is the config and not the
  generator. B2C labels and its golden hashes are untouched.

What changes in default output: the `affected_metrics` value of records that
target those two drivers, in `meta.ground_truth` and `ground_truth.json`. In
the shipped demo configs that is Alpenglow `tees_demand_slump` and
`surprise_rate_spike_2025-12-25` (it lands on `category_demand.shorts`), and
Meridian `legal_backlog`. No raw table changes. `fake-companies score` now
also accepts an anomaly's `root_metric` as a label, so a detector that flags
units for a mix shift still matches.

Each single world's ground truth also gained `metrics_moved`, the metrics
whose total moved, declared or not. For a dq anomaly that moves no metric
(duplicates that staging removes, a loading delay) it is `[]`, which is the
plain statement that its truth is only in `affected_signals`.

**A crash found by sweeping seeds.** A retail session in the last ten minutes
of the final day places its order past the timeline; the returns stage indexed
one row past the driver panel. Fixed by clipping the index, which changes no
run that completed before.

---

*This document is written and maintained by an AI agent (Claude), with human oversight.*
