# Spec: world sets for evaluation

**Status:** phase 1 built, 2026-11-19 (see §8 for what changed on the way). Phase 2 not started, except the pieces of item 6 that §10 pulled forward (2026-11-20, manifest `schema_version` 2).

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
  "schema_version": 2,
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
   *Done 2026-11-20: `docs/canonical_trees.md`, with the scenarios they are
   asked about planted in the demo configs.*
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
6. **Metrics the trees need and the semantic layers lack** (found by
   Breakdown's runner): a `discounted_orders` metric in retail; a stock
   metric in retail and in wholesale (neither vertical has one, so a tree
   there cannot have a stock node); weekly and monthly distinct counts
   (`active_subscriptions`, `dau` at a coarser grain are not derivable from
   the daily series — a second, coarser-grain series file would answer them).
   *Done 2026-11-20 except monthly distinct counts: §10.2 and §10.3.*
7. **Blind sets.** A `worlds --blind` layout that writes ground truth and
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

**Findings from Breakdown's runner, after it read the sets end to end.**

- *A degenerate `moved`.* A series with nothing in its declared window
  (`rel_change` 0, `z_window` 0) was `moved` because the stretched window
  alone cleared the bar. The stretched window now counts only when the
  declared one points the same way with |z| ≥ 1. The same review found a
  larger fault: with too few placebo windows the standard error fell back to
  sigma/√n, which is far too small for noise that persists from day to day
  (product events under a 17-week anomaly "moved" in ten of ten seeds, in
  both directions). Where the twins already differ before the anomaly, the
  standard error is now the measured spread of window-length means over those
  days; where they were aligned, it is the smaller of the placebo spread and
  sigma/√n widened by the lag-one autocorrelation of the difference. No
  cross-seed number in §9 depends on `moved`.
- *Ratios whose sides aggregate differently.* `metrics[].time_aggregation` is
  `ratio_of_sums` only when both sides are sums, otherwise
  `ratio_of_aggregates`, and `input_time_aggregation` gives each side's rule.
- *A stock that is a distinct count.* `metrics[].measure_agg` says so. The
  weekly MetricFlow value (distinct over the week) is not in the series file;
  recorded under phase 2.
- *Roots on numerator counts.* `root_metric_rate` names the rate.
- *Ramps.* `onset` is when the effect clears the noise, by design weeks
  after a ramp starts; `first_diff` is exact when the twins were aligned.
  `docs/using.md` says which to use when.
- *Slices.* Every twin has `<world>.effects_summary.parquet` with the stats
  of every slice in the series file; the effects table covers the same
  slices. The JSON keeps totals only, for size.
- *One verdict per anomaly and metric.* `effects_summary.parquet` has
  `verdict` (established up or down at two-sided 1%, open, exactly zero).
- *Structured formulas.* `metrics[].formula` is the derived expression as a
  tree.
- *Averages.* Their series carry the sum and the row count as
  `numerator` / `denominator`, so `sales_cycle_days` can be served weekly.
- The remaining gaps (a discounted-orders metric, stocks in retail and
  wholesale, coarser-grain distinct counts) are listed under phase 2.

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

## 9. The ten-seed study (2026-11-19)

Ten-seed world sets (seeds 0–9, baseline regime, null plus every single,
series and effects, `--no-raw`, three jobs on a 12-core laptop) for the four
demo configs, built after the label corrections of §8.

**Which rule these numbers were measured under.** This section was first
written from sets built before the two `moved` fixes recorded at the end of
§8. All four sets were then rebuilt at commit `b51d70e` (the current rule:
the standard error of a window mean comes from the twins' divergence before
the anomaly where they already differ, otherwise from the smaller of the
placebo spread and the autocorrelation-widened σ/√n), and the study was run
again on the rebuilt sets (2026-11-20):

| Set | Worlds | Build time at `b51d70e` | Size (no raw tables) |
|---|---|---|---|
| Bristlecone (cpg_wholesale) | 80 | 11 s | 32 MB |
| Meridian (b2b_services) | 80 | 47 s | 61 MB |
| Alpenglow (retail_dtc) | 80 | 9 min 32 s | 149 MB |
| White Cube (b2c_saas) | 110 | 3 h 47 min wall clock | 240 MB |

White Cube took 27 to 30 minutes in the two earlier builds; its CPU time rose
only from 1.8 to 2.5 hours, so most of the wall clock is the laptop, not the
code. The sets are larger than in the first build because `83640ca` added a
per-twin `effects_summary.parquet` and slices to the effects tables.

**Every cross-seed number in this section came out identical on the rebuild,
to every digit printed.** That is expected: the rule changes how one twin is
judged, not the twin series, and every number below is computed from the
series. What did change is the per-twin `moved` flag, which §9.6 reports.
No conclusion of §9.1 to §9.5 flipped.

All numbers below are the relative change of the window mean (single world
over null world, minus one) over the anomaly's declared window, averaged over
the ten seeds, with its standard error (sd across seeds / √10).

**Criterion.** An effect is *established* when |mean| / SEM ≥ 3.25, the
two-sided 1% point of t with 9 degrees of freedom. 1% and not 5% because
about seventy (anomaly, metric) pairs are tested. Otherwise it is *open*.

### 9.1 What ten seeds settled

Every root is established in the declared direction in 10 of 10 seeds, for
all 19 scripted rate anomalies (four each in Alpenglow, Meridian and
Bristlecone, seven in White Cube; the first version of this section said 15,
which was a miscount).

**Alpenglow: everything declared is now established.** The six entries that
were unclear at three seeds are settled, all in the declared direction:

| Anomaly | Metric | Effect | SEM | t |
|---|---|---|---|---|
| mobile_checkout_bug | orders | −3.9% | 0.44 | −8.9 |
| | units | −3.9% | 0.39 | −9.8 |
| | gross_revenue | −3.5% | 0.73 | −4.8 |
| | net_revenue | −4.0% | 0.77 | −5.2 |
| paid_social_budget_cut | orders / units / gross_revenue / net_revenue | −5.6% each | 0.40–0.50 | −11 to −14 |
| tees_demand_slump | gross_revenue +4.5%, net_revenue +4.8%, gross_margin +3.9%, aov +4.6%, margin_rate −0.6% | | | 13 to 23 |
| jackets_sizing_recall | return_rate +11.5%, refunds +27.8%, net_revenue −2.3% | | | 25 to 38 |

**Bristlecone: everything established, to three or four digits.** The twin
streams are aligned, so one seed is enough for any effect there.

**Meridian: settled except the tail of one anomaly.**

| Anomaly | Established | Still open |
|---|---|---|
| outbound_team_turnover | leads −18.0%, qualified_deals −14.7% (t −8.7), proposals_sent −11.4% (t −4.6), open_deals −11.3%, open_pipeline_value −8.9% (t −3.6) | negotiations −2.6% ± 4.4, deals_won −1.1% ± 7.5, bookings +1.5% ± 9.8, invoiced_revenue −8.3% ± 3.2 (t −2.6) |
| healthcare_proposal_stall | all five: negotiations −8.5%, deals_won −6.0%, win_rate −6.2%, bookings −6.2% (t −6.1), invoiced_revenue −2.3% | none |
| discounting_creep | all four: avg_deal_size −7.8%, bookings −7.5%, invoiced_revenue −3.1%, open_pipeline_value −15.5% | none |
| legal_backlog | all eight, including the five added in §8: proposals_sent −16.9%, negotiations −29.3%, deals_won −30.8%, bookings −31.2%, invoiced_revenue −12.1% | none |

The four open entries of `outbound_team_turnover` are open for two reasons at
once. The lead cut is four weeks long and a deal needs weeks to reach
negotiation, so inside the declared window the expected effect on the late
stages is close to zero; and this is the one Meridian anomaly whose twins
diverge from day one, so the noise is at its worst (§9.3). Over the window
plus 90 days the late stages come out as negotiations −4.7% (t −2.6),
deals_won −4.3% (t −3.0), bookings −0.6% (t −0.1): still not established.

**White Cube: the funnel top is settled; new subscriptions and new MRR are
not, for five of seven anomalies.**

| Anomaly | Established | Still open |
|---|---|---|
| latam_campaign_spend_ramp | marketing_spend +9.1%, sessions +5.8%, signups +4.9% (t 9.6), trials_started +5.7% (t 4.6) | new_subscriptions +2.9% ± 2.3, new_mrr +1.4% ± 2.3, mrr +0.3% ± 0.8 |
| brazil_lift_paid_social | visit_signup_rate +2.8%, signups +2.8%, trials_started +2.6% (t 8.4) | new_subscriptions +2.4% ± 1.5, new_mrr +2.5% ± 2.0 |
| brazil_lift_organic | visit_signup_rate +3.0%, signups +3.0%, trials_started +2.8% | new_subscriptions +1.6% ± 1.2, new_mrr +2.3% ± 1.6 |
| brazil_lift_referral | visit_signup_rate +0.9%, signups +0.9%, trials_started +0.75% (t 4.0) | new_subscriptions +1.4% ± 1.0, new_mrr +0.3% ± 1.1 |
| onboarding_revamp | trial_days_active +89%, trial_activation_rate +41%, trial_conversion_rate +29%, trial_conversions +29%, new_subscriptions +17%, new_mrr +17% | product_events +1.4% ± 0.7 (t 1.9; it looked settled at three seeds and is not) |
| mobile_signup_cta_regression | visit_signup_rate −6.75%, signups −6.75%, trials_started −7.0% | new_subscriptions −3.1% ± 1.1 (t −2.8), new_mrr −3.3% ± 1.1 (t −2.9) |
| professional_churn_spike | all five: churned_subscriptions +28%, customer_churn_rate +30%, churned_mrr +49%, active_subscriptions −1.4%, mrr −2.2% | none |

Newly settled against the three-seed table: the ramp's and the referral
lift's trials_started (both up). For the spend ramp, new_subscriptions
(+5.8%, t 4.6) and new_mrr (+5.3%, t 3.7) are established over the window
plus 90 days: a ramp holds its level, so the effect keeps arriving. Every
open entry has the declared sign in its mean; none is contradicted.

Data-quality anomalies: in every seed, `pos_feed_outage` moves pos_units and
pos_dollars, `orders_session_id_nulls` moves session_orders,
`deals_closed_at_nulls` moves open_deals and open_pipeline_value,
`product_pipeline_outage` moves product_events, dau, wau and the paying-activity metrics, and the other eight move no metric (`metrics_moved: []`).

### 9.2 How precisely the effect size is known

Relative precision is SEM / |effect| at ten seeds. "Seeds for ±20%" is the
number of seeds at which the 95% interval is within ±20% of the effect:
(1.96 · sd / (0.2 · |effect|))².

| Anomaly | Root (slice) | Precision | Seeds ±20% | Top of tree | Effect | Precision | Seeds ±20% |
|---|---|---|---|---|---|---|---|
| Alpenglow mobile_checkout_bug | conversion_rate[paid_search, mobile] −46.0% | 3.3% | 2 | net_revenue | −4.0% | 19% | 35 |
| Alpenglow jackets_sizing_recall | returned_units[jackets] +80.7% | 3.7% | 2 | net_revenue | −2.3% | 3% | 1 |
| Alpenglow paid_social_budget_cut | marketing_spend[paid_social] −50.0% | 0.0% | 1 | net_revenue | −5.7% | 9% | 8 |
| Alpenglow tees_demand_slump | units[tees] −25.0% | 1.4% | 1 | net_revenue | +4.8% | 5% | 3 |
| Meridian outbound_team_turnover | leads[outbound] −43.9% | 3.4% | 2 | bookings | +1.5% | 670% | about 43,000 |
| Meridian healthcare_proposal_stall | negotiations[healthcare] −37.1% | 5.6% | 3 | bookings | −6.2% | 16% | 27 |
| Meridian discounting_creep | avg_deal_size −7.8% | 5.2% | 3 | bookings | −7.5% | 3% | 2 |
| Meridian legal_backlog | sales_cycle_days +15.2% | 4.7% | 3 | bookings | −31.2% | 5% | 3 |
| Bristlecone, all four | exact to 0.1–0.2% | | 1 | wholesale_net_revenue | −7.5%, −7.8%, 0 (decoupled), +6.6% | ≤ 2% | 1 |
| White Cube latam_campaign_spend_ramp | marketing_spend[paid_social] +17.5% | 0.3% | 1 | mrr (level at window end) | +0.45% | 180% | about 3,100 |
| White Cube brazil_lift_paid_social | visit_signup_rate[paid_social, BR] +122% | 5.5% | 3 | mrr | +1.4% | 53% | 272 |
| White Cube brazil_lift_organic | visit_signup_rate[organic, BR] +129% | 3.5% | 2 | mrr | +2.1% | 26% | 68 |
| White Cube brazil_lift_referral | visit_signup_rate[referral, BR] +103% | 10.5% | 11 | mrr | +0.3% | 282% | about 7,700 |
| White Cube onboarding_revamp | trial_days_active +89% | 0.6% | 1 | mrr | +4.5% | 20% | 39 |
| White Cube mobile_signup_cta_regression | visit_signup_rate[paid_social, mobile] −48.9% | 1.2% | 1 | mrr | −0.7% | 34% | 112 |
| White Cube professional_churn_spike | churned_subscriptions[professional] +105% | 5.4% | 3 | mrr | −3.2% | 8% | 6 |

So measured effects can serve as ground truth for attribution sizes at the
root everywhere (ten seeds give 5% or better), and at the top of the tree for
CPG, for the aligned retail and B2B anomalies, and for retail spend and
conversion anomalies with 10 to 35 seeds. They cannot for a B2B lead-volume
anomaly's effect on bookings, nor for the effect on MRR of any White Cube acquisition
anomaly (68 to several thousand seeds). For those, the size at the top of the
tree has to come from the generator's own arithmetic or from a much larger
magnitude, not from twins.

### 9.3 Which metrics have usable twin truth

The per-twin noise of a metric is the spread across seeds of its window
relative change. It depends on the anomaly: zero or near it when the twins'
streams stay aligned, large when they diverge from day one. The table gives
the worst anomaly for each metric, the smallest effect ten seeds can
establish against that noise, and the seeds needed to pin a 5% effect to
±20%.

**CPG.** Every metric: noise 0.2–0.6%, one seed is enough. Usable.

**Retail** (worst case is the conversion or spend anomaly):

| Metric | Per-twin noise | Smallest effect at 10 seeds | Seeds for a 5% effect ±20% |
|---|---|---|---|
| sessions, marketing_spend, margin_rate | 0.3–0.4% | 0.3–0.5% | 1 |
| new_customers, converting_sessions, units, orders, conversion_rate | 1.0–1.4% | 1.1–1.4% | 5–8 |
| aov, gross_margin, gross_revenue, payments_captured, net_revenue | 1.9–2.4% | 2.0–2.5% | 14–23 |
| repeat_order_share, returning_customer_orders | 4–5% | 4–5% | 63–95 |
| refunds, returned_units, return_rate | 9–11% | 9–11% | 310–460 |
| payment_failures | 16% | 17% | about 1,000 |

Usable at ten seeds: everything down to net revenue for effects of 2.5% or
more. Returns metrics are usable only under the return-rate and
category-demand anomalies, where the streams are aligned (smallest
establishable effect 1.1–2.4%);
under a conversion or spend anomaly they are not usable at any practical seed
count.

**B2B** (worst case is `leads.outbound`, the only anomaly that re-deals the
pipeline; under the other three the noise is 0–8%):

| Metric | Per-twin noise | Smallest effect at 10 seeds | Seeds for a 5% effect ±20% | for a 20% effect |
|---|---|---|---|---|
| leads, deals_lost, open_deals, closed_deals | 2.4–3.5% | 2.5–3.6% | 23–47 | 2–3 |
| sales_cycle_days, qualified_deals | 4.7–5.4% | 4.9–5.5% | 87–111 | 6–7 |
| open_pipeline_value, proposals_sent | 7.8% | 8.0% | 230 | 15 |
| invoiced_revenue | 10% | 10.5% | 400 | 26 |
| negotiations | 14% | 14% | 740 | 47 |
| avg_deal_size, collected_cash, deals_won, win_rate | 19–24% | 19–25% | 1,400–2,200 | 86–140 |
| bookings | 31% | 32% | about 3,700 | 230 |

The suspicion was right: under a lead-volume anomaly, B2B from negotiations
down has no usable twin truth at any practical seed count. A few dozen deals
close in a window and each is a lognormal amount, so two realizations of the
same company differ by 30% in window bookings. Under stage-conversion,
deal-size and cycle-time anomalies the twins are aligned up to the affected
stage and the same metrics are usable at ten seeds.

**B2C** (worst case is the spend ramp, which re-deals sessions; MRR and
active subscriptions are stocks, so their noise is a level difference):

| Metric | Per-twin noise | Smallest effect at 10 seeds | Seeds for a 5% effect ±20% |
|---|---|---|---|
| marketing_spend, sessions, wau | 0.1–0.7% | 0.1–0.7% | 1–2 |
| visit_signup_rate, signups, arpu, dau | 1.4–1.8% | 1.5–1.8% | 8–13 |
| active_subscriptions, mrr, trial_days_active, payments | 2.5–3.1% | 2.6–3.2% | 24–37 |
| trials_started, trial_activation_rate, member_activity_rate, product_events | 3.6–4.7% | 3.7–4.8% | 51–84 |
| new_subscriptions, new_mrr, revenue, net_new_mrr, trial_conversion_rate, trial_conversions | 7–10% | 7–10% | 200–360 |
| churned_subscriptions, churned_mrr, customer_churn_rate, reactivations | 10–12% | 10–12% | 360–540 |
| expansion_mrr, failed_payments, payment_failure_rate | 22–24% | 23–25% | 1,900–2,300 |
| contraction_mrr | 95% | 97% | about 34,000 |

Usable at ten seeds: traffic, signups, trials started, and the stocks for
effects of 3% or more. New subscriptions and new MRR need an effect of 7% or
more (the onboarding revamp qualifies; the acquisition anomalies, at 1–3%,
do not at any practical seed count). Churn metrics are usable under a churn
anomaly (established at t 13–16) because the effect is large, not because the
noise is small. Upgrade and downgrade MRR have no usable twin truth.

### 9.4 Stories too small to see from the top of the tree

Criterion: the anomaly's effect on the top-of-tree metric inside its window
(cross-seed mean) is smaller than that metric's week-to-week noise, taken as
the robust standard deviation of the week-over-week relative change of the
weekly metric in the ten null worlds. That noise still contains growth and
seasonality a model would explain, so it is an upper bound; "expected z" is
the more generous reading, effect × √(weeks in window) over the per-week
noise (the week-over-week figure divided by √2).

| Company (top metric, weekly noise) | Anomaly | Effect at top | Effect / noise | Expected z over the window |
|---|---|---|---|---|
| Alpenglow (net_revenue, 7.3%) | mobile_checkout_bug | −4.0% | 0.55 | 1.0 |
| | jackets_sizing_recall | −2.3% | 0.31 | 1.0 |
| | paid_social_budget_cut | −5.7% | 0.77 | 1.9 |
| | tees_demand_slump | +4.8% | 0.66 | 1.3 |
| Meridian (bookings, 48%) | outbound_team_turnover | +1.5% (not established) | 0.03 | 0.1 |
| | healthcare_proposal_stall | −6.2% | 0.13 | 0.4 |
| | discounting_creep | −7.5% | 0.16 | 0.6 |
| | legal_backlog | −31.2% | 0.65 | 2.2 |
| Bristlecone (wholesale_net_revenue, 7.6%) | teas_demand_scare | −7.5% | 0.99 | 2.0 |
| | tinctures_viral_spike | +6.6% | 0.87 | 1.7 |
| | brand_budget_cut | 0 (decoupled by design) | 0 | 0 |
| White Cube (mrr, 1.3%) | latam_campaign_spend_ramp | +0.45% (not established) | 0.35 | 1.2 |
| | brazil_lift_referral | +0.3% (not established) | 0.20 | 1.0 |
| | mobile_signup_cta_regression | −0.7% (not established) | 0.56 | 1.9 |

Those fourteen (thirteen without the deliberately decoupled one) cannot test localization from the top: a tool that starts at
revenue, MRR or bookings has no gap there to explain. Five clear the bar:
Bristlecone `whole_earth_delisting` (−7.8%, ratio 1.03, z 3.6) and White Cube
`brazil_lift_paid_social` (+1.4%, 1.09, z 5.3), `brazil_lift_organic`
(+2.1%, 1.61, z 7.9), `onboarding_revamp` (+4.5%, 3.4, z 20) and
`professional_churn_spike` (−3.2%, 2.5, z 10.6).
Every Alpenglow and Meridian story is below the bar. They remain good tests
of detection and attribution at or near the root, where every effect is
several times the noise.

### 9.5 What keeping twins aligned would take (not done; for a later decision)

Read off the builders, not measured: nothing below was prototyped. One
measurement does bear on it, and the current `moved` rule makes it visible:
for how many of the metrics an anomaly touches (verdict not exactly zero)
were the twins already different *before* the anomaly started? That is
layer 1 below, directly observed.

The unit is the metric total, not the anomaly. Corrected 2026-11-20: the
first version of this table had an "anomalies aligned until the start"
column that read "none" for White Cube, which was true only in the sense
that no White Cube anomaly has *every* metric aligned. The metrics nearest
each root are aligned, and Breakdown's runner rightly found
`marketing_spend` and `visit_signup_rate` aligned on the smoke set.

| Set | Anomaly | Metric totals that differ before the start | Metric totals aligned until the start |
|---|---|---|---|
| Bristlecone | all four | none | all |
| Meridian | `outbound_team_turnover` | 15 of 15 | none |
| | `healthcare_proposal_stall`, `legal_backlog`, `discounting_creep` | at most 1 | the rest, including every pipeline stage and bookings |
| Alpenglow | `mobile_checkout_bug` | 16 of 20 | `conversion_rate`, `converting_sessions`, `new_customers`, `session_orders` |
| | `paid_social_budget_cut` | 21 of 22 | `marketing_spend` |
| | `jackets_sizing_recall`, `tees_demand_slump` | none | all |
| White Cube | `latam_campaign_spend_ramp` | 37 of 38 | `marketing_spend` |
| | the three Brazil lifts, `mobile_signup_cta_regression` | 33 of 36 | `signup_sessions`, `signups`, `visit_signup_rate` |
| | `professional_churn_spike` | 29 of 32 | `direct_conversions`, `trial_conversions`, `trial_conversion_rate` |
| | `onboarding_revamp` | 32 of 32 | none |

Slices can differ from their totals (a root slice is often aligned when its
total is not). The set's `effects_summary.parquet` now says so for every
(anomaly, metric, slice): `aligned_before_start` (§10.5).

These are the same anomalies whose downstream metrics are noisy in §9.3, and
the same ones where `moved` now takes its standard error from the
pre-anomaly divergence. Nothing about the coupling plan below changed with the
rebuild.

The noise has two layers, and they need different fixes.

**Layer 1: draws that are not in time order, and streams shared across
branches.** This is what makes a twin differ *before* the anomaly and in
branches the anomaly cannot reach. It is fixable without touching the
statistical model:

| Builder (stream) | What breaks alignment | Change |
|---|---|---|
| `shared/traffic.py` (`traffic`) | One stream for all channels, consumed channel by channel; within a channel each attribute (time of day, country, device, duration, page views, landing page) is drawn as one array over all sessions, so a different session count re-deals every later attribute and every later channel. | A stream per channel and attribute (`traffic.<channel>.<attribute>`). Then a channel the anomaly does not touch is identical, and rows before the anomaly are identical. |
| `shared/marketing.py`, `cpg .../marketing.py` (`marketing`) | One stream across channels and campaigns. | A stream per channel and campaign. |
| `retail .../orders.py` (`retail_orders`) | Conversion uniforms are per session in time order (aligned); names are two arrays drawn after them. | Names from their own stream (cosmetic: no metric reads them). |
| `retail .../repeat.py` (`retail_repeat`) | Frailty per customer, then one uniform per (customer, month) in customer order, then day, time and channel arrays. One extra customer shifts every later customer's repeat draws for all months. | Separate streams for frailty, the hit matrix, and each attribute; draw the hit matrix month-major. Still shifts when the customer count changes (layer 2). |
| `retail .../basket.py`, `fulfillment.py`, `returns.py`, `payments.py` | Attribute arrays drawn one after another over all orders or lines. | A stream per attribute; lines-per-order from its own stream. |
| `b2b .../pipeline.py` (`b2b_pipeline`) | Lead counts source by source, then about fifteen attribute arrays over all deals, all from one stream. | A stream per source for counts and per attribute for the rest. |
| `b2c .../funnel.py`, `lifecycle.py`, `usage.py`, `billing.py` | Same pattern; the lifecycle walks months and draws per alive subscription. | A stream per decision (convert, churn, upgrade, ...) and per month. |
| `corruption/loading.py` (`loading`) | One stream consumed table by table. | A stream per table. |

All of these rename or split existing streams, which the hard rules forbid
today: every one moves the B2C golden hashes (traffic, marketing, funnel,
lifecycle, usage, billing and loading are all on the B2C path) and the bytes
of every published demo bundle. Mechanically it is small per file and touches
about twenty files.

**Layer 2: entity position.** After layer 1 a twin still diverges from the
anomaly onward, because a draw is tied to an entity's *position* in an array.
One session fewer on day t and every later session of that channel takes its
neighbour's draws; one customer more and every later customer does. This is
the layer that makes B2B downstream and retail orders noisy, and no amount of
stream splitting removes it. Removing it means keying each draw to the
entity's identity instead of its position:

- counts per (channel, day) from a generator spawned for that day
  (`RngHub.spawn` already exists), with the day's sessions drawn from the same
  generator, so a change on one day leaves the other days alone; a smaller
  count keeps a prefix of the same sessions (thinning) instead of a new draw;
- every downstream decision drawn from a hash of (stream name, stable entity
  key), where the key is (channel, day, ordinal) for a session and inherits
  down the chain (customer = its converting session, order = customer and
  month, line = order and line number, deal = source, day and ordinal). A
  vectorized integer hash to a uniform replaces `gen.random(n)`.

With that, a single world would differ from its null world only in entities
the anomaly actually changes, and a twin difference would be the causal
effect with almost no realization noise, as it already is in CPG. It is a
rewrite of the entity layer's randomness in all four verticals, it changes
every generated byte, and the statistical tests would need re-running against
the new draws. Poisson and binomial marginals are unchanged.

A cheaper partial step that needs no rewrite: **thinning for rate decreases**.
Session conversion already works this way (one uniform per session against a
probability), which is why conversion and return-rate anomalies are aligned.
Applying the same to lead and session *counts* (draw the null count, keep each
entity with probability magnitude) would align the decrease anomalies
(budget cuts, lead drops) but not increases.

### 9.6 One twin's `moved` under the current rule

`moved` is a per-world flag; the cross-seed `verdict` is the per-anomaly
answer. On the rebuilt sets:

- **Roots: moved in 10 of 10 seeds for all 19 rate anomalies.** In the
  intermediate build (rule of `83640ca`, before `b51d70e`) White Cube's spend
  ramp root, +17.5% with no noise, was moved in 0 of 10; it is 10 of 10 again.
- **Metrics with an exactly-zero verdict: never moved** (0 of 306 pairs
  across the four sets).
- **Metrics with an established verdict:** moved in 70–100% of seeds on
  average (White Cube 70%, Alpenglow 75–91%, Meridian 80–96%, Bristlecone
  100%). Small established effects under divergent twins are where one twin
  misses: Alpenglow `mobile_checkout_bug` gross and net revenue (2 of 10),
  Meridian `outbound_team_turnover` proposals and open pipeline value (3 and
  1 of 10), White Cube brazil referral trials_started (0 of 10).
- **Metrics with an open verdict:** moved in 5% of seeds on average in White
  Cube (55 of 187 pairs move in at least one seed, one in half the seeds),
  1% in Alpenglow. Under the earlier rule product events under the 17-week
  onboarding anomaly moved in 10 of 10; now 1 of 10.
- **Moved every time, with an open verdict, and both right.** Meridian
  `healthcare_proposal_stall` moves `avg_deal_size` and `sales_cycle_days` in
  10 of 10 seeds while neither is established across seeds. The twins are
  aligned until the anomaly, and in each seed it really does change those
  averages (by removing healthcare deals from the closed mix), but the sign
  depends on which deals the seed had: −12% to +4% for deal size. The same
  holds for Bristlecone `avg_case_price` under the delisting (5 of 10). Per
  world the effect is real; there is no expected effect to score.
- **Established, never moved in the window, also both right.** Meridian
  `invoiced_revenue` under `healthcare_proposal_stall` (−2.3%) and
  `legal_backlog` (−12.1%): invoicing lags, and its median onset is about
  140 days after the anomaly starts. Use the window plus 90 days or `onset`.

Changes against the sets the first version of this section was written from
(before `83640ca`), all in this flag and none in a cross-seed number. Fewer
spurious moves: White Cube onboarding `product_events` 10 → 1 of 10 and
`trial_conversion_rate` 10 → 5; Meridian `outbound_team_turnover`
`open_pipeline_value` 6 → 1. More real ones found: White Cube spend-ramp
signups 0 → 10 and the organic lift's trials_started 6 → 10; Alpenglow
`paid_social_budget_cut` orders and units 1–2 → 10, gross revenue 1 → 7, net
revenue 1 → 4, and `mobile_checkout_bug` orders and units 0 → 6; Meridian
`outbound_team_turnover` qualified deals 2 → 4 and proposals 1 → 3. The rest
moved by one or two seeds either way. Bristlecone did not change.

### 9.7 What this means for using measured effects as ground truth

- **Direction and existence at the root:** settled for all nineteen, with
  any seed count.
- **Size at the root:** known to 5% or better at ten seeds (the Brazil
  referral lift, 10%, is the one exception).
- **Size at the top of the tree:** usable for CPG (exact), for B2B deal-size,
  stage and cycle-time anomalies (3–16%), for retail (3–19%; 35 seeds bring
  the checkout bug to ±20%), and in B2C for the churn spike and the
  onboarding revamp. Not usable for a B2B lead-volume anomaly or for the B2C
  acquisition anomalies, where the limit is the coupling of §9.5 and not the
  number of seeds anyone will build.
- **Scoring attribution sizes** (the question Breakdown's runner deferred):
  yes, against the cross-seed mean, for the (anomaly, metric) pairs whose
  `verdict` is established and whose relative precision in §9.2 is about
  20% or better; those are the root of every anomaly and the top-of-tree
  cases in the previous bullet. Score the others on direction and
  localization only. Do not score a size against one twin's difference where
  the twins diverge before the anomaly (§9.5 table, per metric; the set's
  `aligned_before_start` says it per metric and slice): that difference
  carries realization noise as large as the effect. Where they are aligned
  (all of Bristlecone; Meridian except the lead cut; Alpenglow returns and
  category demand; and the metrics nearest the root elsewhere, listed in the
  §9.5 table), one twin's difference is that world's own effect, exact in
  Bristlecone and within the per-twin noise of §9.3 elsewhere (layer 2 still
  re-deals entities after the start), and can be scored per world when that
  noise is small next to the effect. The rebuild under the current `moved`
  rule changed none of this.

## 10. Contract changes asked for by Breakdown's runner (2026-11-20)

After moving to the `b51d70e` contract the runner asked for four things. Each
is below with the design chosen and why.

### 10.1 Verdicts for small sets

**The problem.** The strict verdict tests the cross-seed mean at two-sided 1%.
With three seeds that needs |t| ≥ 9.9, so on a smoke-tier set even a +33%
churn spike stays `open` and the runner has no target.

**Design.** The strict `verdict` is unchanged and stays the one to pick
targets by. Beside it, in `effects_summary.parquet`:

- `verdict_10pct`: the same test (cross-seed mean over its standard error,
  Student's t with n − 1 degrees of freedom) at **two-sided 10%**. Same
  values as `verdict`. At three seeds it needs |t| ≥ 2.92;
- `seeds_to_establish` and `seeds_to_establish_10pct`: for each row, the
  fewest seeds at which this set's mean and spread would be established at
  that level. A projection that assumes the mean and sd hold; NULL when
  there is nothing to establish (`exactly_zero`, a zero or undefined mean,
  one seed).

`worlds.json` gains `verdicts`, one entry per verdict column, strict first:
`column`, `level` (two-sided), `t_critical` at this set's seed count (null
below `min_seeds`), `min_seeds` (2: with one seed there is no spread and
nothing is established at any level) and `seeds_to_establish_column`.

**Why this shape.**

- *A second column, not a level parameter on one column.* A reader that
  knows only `verdict` keeps reading the strict answer; nothing it already
  trusts changes meaning. The level is in the column name and in the
  manifest, so the two cannot be confused by a reader that looks at either.
- *10%, two-sided.* It is the one-sided 5% test in either direction, the
  usual bar for a directional claim, and the runner's targets come with a
  declared direction. 5% two-sided would need |t| ≥ 4.3 at three seeds and
  still miss much of what the smoke sets are built to show. The cost is
  stated, not hidden: among pairs with no effect, about one in ten is
  established by chance. `exactly_zero` pairs are excluded by construction
  (no seed's twins differ), so that rate applies only to pairs whose twins
  differ, which is most pairs in B2C and few in CPG.
- *The loose verdict never contradicts the strict one.* Anything
  established at 1% is established at 10% in the same direction; the loose
  one can only settle more. A test pins this.
- *Seeds to establish per row, not one number per set.* The minimum seed
  count that *can* establish anything is always 2 and says little; what the
  runner needs to plan a bigger set is the projected count per target, which
  depends on that target's effect and spread.

### 10.2 Weekly distinct counts

Pulled forward from phase 2 item 6, week grain only.

**The problem.** A `count_distinct` metric counts distinct entities per day.
MetricFlow at week grain counts them over the week, which the days cannot
give: DAU summed over a week counts a user once per active day, and the
last day's active subscriptions miss every subscription that ended earlier
in the week. The runner was serving these at week grain from last-day counts.

**Design.** Every world whose vertical has such a metric gets
`<world>.weekly_series.parquet`: `date` (the Monday that starts the week, as
MetricFlow labels `metric_time__week`), `metric`, `dimension`, `value`,
`metric_value`, `days`. It holds every simple `count_distinct` metric
(`SemanticLayer.weekly_metrics`): in B2C `dau`, `paying_active_users` and
`active_subscriptions`; no other vertical has one. Totals and every
single-dimension slice, as in the daily file. Every week that overlaps the
timeline is present, a week with no rows at the metric's fill. `days` is the
number of timeline days in the week, fewer than 7 only at the two edges,
where the count covers just the days that exist (as MetricFlow's does); drop
those weeks if a partial week would mislead.

Manifest: `worlds[].weekly_series` (the path, null when the vertical has no
such metric or series are off) and `metrics[].has_weekly_series`.

`active_subscriptions` is both a stock (`time_aggregation: end_of_period`,
the last day's count) and in the weekly file (distinct subscriptions active
on any day of the week). They answer different questions; the weekly file
is MetricFlow's number.

**Verified against MetricFlow** in `tests/test_semantic_matches_metricflow.py`:
for each distinct-count metric, `mf query --group-by metric_time__week`, the
total and every single-dimension slice, equal to the file on every week.

**Not in the file, and why.** `wau` is a cumulative metric (a trailing
7-day distinct count, computed daily), so its week value is a day of the
daily series and needs no file. MetricFlow 0.13 answered it with the week's
*first* day (`FIRST_VALUE`, its default `period_agg`: the seven days ending
that Monday, mostly the previous week) while the manifest's rule took the
last; since §10.10 the metric declares `period_agg: last` and the two agree.
The same holds for the cumulative stocks added in §10.3. Monthly grain is
not built.

### 10.3 Metrics the trees needed: discounted orders, and a stock in retail and wholesale

Pulled forward from phase 2 item 6. Each is a dbt metric in the vertical's
semantic layer, so it is in every world's series file, the effects tables and
`worlds.json` like any other, and `tests/test_semantic_matches_metricflow.py`
holds it to `mf query` (totals and every slice).

| Vertical | Metric | Definition | Kind |
|---|---|---|---|
| retail_dtc | `discounted_orders` | orders with `discount_amount > 0` (a measure on `fct_orders`) | flow, `sum` |
| retail_dtc | `customer_base` | customers acquired to date: the running total of the `new_customers` measure | stock (cumulative, no window) |
| cpg_wholesale | `trade_inventory_build` | units shipped into retail less units sold through, summed from the first scanned week (new mart `fct_inventory_flows`) | stock (cumulative, no window) |

**Why these stocks.**

- *Retail: the customer base.* It is the stock that repeat orders are drawn
  from, which is how Alpenglow's tree already uses it (it bound its own SQL
  for `customer_base`; it can now use the metric of the same name). It is a
  level that only grows, and an acquisition anomaly (spend cut, checkout bug)
  leaves it permanently lower by the customers not acquired. Active customers
  in a trailing year were the alternative, but on a two-year timeline that
  window is not full for the first year, so the level would mostly measure
  the start of the data.
- *Wholesale: retail inventory, as a build.* Sell-in (shipments) less
  sell-through (POS) is the stock a CPG analyst watches between those two
  flows, and it gives the tree an exact identity: shipped units = POS units +
  the change in inventory. The generator does not model retailer stock: each
  account reorders last week's sales. So the opening level is unknown and the
  metric is the build *since the data starts*, zero on the first scanned
  day, negative when retailers destock. Two consequences are documented on
  the metric: orders replenish last week's sales without correcting for
  stock on hand, so the level drifts like a random walk (on Bristlecone it
  ends about a week of sales above where it started); and a demand anomaly
  builds inventory for about a week before orders follow, then holds that
  level, which is the replenishment lag seen as a stock. An absolute level
  would need an opening stock the data does not contain; a constant would
  only hide that. For the same reason its *relative* change is relative to
  an arbitrary level: score its effects in units (`mean_diff_mean`), not
  `rel_change_mean`.
- *POS is spread over its week.* The POS feed reports a week of units on
  its Monday. Taken as is, the daily balance would drop by a week of sales
  every Monday and climb back as the shipments land; spread evenly over the
  seven days it is a level a daily model can read. Shipments outside the
  scanned weeks are left out so both sides cover the same days; after the
  last scanned week the level stays where it was.

**How cumulative metrics without a window are computed.** `semantic.py`
already evaluated windowed cumulative metrics (`wau`). A cumulative metric
with no window is MetricFlow's running total since the data starts; it is
now supported for additive measures (`sum`, `count`, `sum_boolean`), by
adding up day totals, and refused by name for anything else (a running
distinct count is not a sum of days). Both stocks are in the verticals'
`STOCK_METRICS`, so the manifest says `time_aggregation: end_of_period`.

**Declared in `affected_metrics` (Devon's decision, 2026-11-20; at first
they were left out because it changes `meta.ground_truth` in the default
retail and CPG output).**

- `customer_base` for `spend.*`, `sessions.*` and `conversion_rate.*`: the
  drivers that declare `new_customers`, of which it is the running total.
- `trade_inventory_build` for `consumer_demand.*` and `velocity.*`: POS moves
  at once and shipments a week later, so the build moves. Not for `spend.*`,
  which stays decoupled from sales.
- `discounted_orders` for `spend.*`, `sessions.*`, `conversion_rate.*` and
  `repeat_rate` **only when the anomaly's window overlaps one of the
  config's promotions** (an open-ended window overlaps every later one).
  Promo codes exist only inside a promotion, so outside one there is
  nothing to move. That makes this the one declaration that depends on the
  anomaly's dates; retail supplies it through
  `affected_metrics_in_window(cfg, driver, start, end)`, which `generate`
  uses when a vertical has it. Drivers that cannot change how many orders
  there are (`items_per_order`, `category_demand.*`, `return_rate.*`)
  never declare it.

Data-quality anomalies declare no metrics, as before (their truth is
`affected_signals`). Measured on proof sets, the POS feed outage does move
`trade_inventory_build` (the build is computed from POS rows), and
`metrics_moved` records it.

Checked against measured effects: on Bristlecone (3 seeds) the build is
established in the declared direction under all three demand and velocity
anomalies and exactly zero under the brand budget cut; on the retail smoke
set (3 seeds) `customer_base` is established down (t −10.5) under the
checkout regression, whose window overlaps the smoke promotion, while
`discounted_orders` there is declared and still open (−3.6%, t −0.3: a
small effect at three seeds). On Alpenglow (10 seeds) `customer_base` is
established down under the checkout bug (t −18.5) and under the budget cut
at 10% (t −2.3; open at 1%: a level's noise is large next to the customers a
three-week cut loses, though `new_customers` there is t −31.6), and exactly
zero under the tees mix shift; `discounted_orders` is undeclared for all
three (no promotion in their windows) and measured zero in every seed.

### 10.4 `schema_version` 2

`worlds.json` → `schema_version` was 1 from the first build, while the
contract changed under it twice (`83640ca`, `b51d70e`). It is now **2**, and
`fake_companies.worlds.SCHEMA_VERSION` documents what each value guarantees:

| Version | Built | What a reader may rely on |
|---|---|---|
| 1 | until 2026-11-19 | Worlds and twins; ground truth with `measured_effects`; `*.effects.parquet`; `*.series.parquet`; `effects_summary.parquet` with `verdict`. Sets built between `83640ca` and `b51d70e` also say 1 and carry part of version 2, and nothing in them says which `moved` rule produced them, so treat every version 1 set as old. |
| 2 | from 2026-11-20 | Everything in 1, plus: the `moved` rule of `b51d70e` (§8); `metrics[]` `measure_agg`, `input_time_aggregation`, `formula`, `has_sides`; per-twin `*.effects_summary.parquet` and slice rows in the effects tables; `verdicts`, `verdict_10pct`, `seeds_to_establish`, `seeds_to_establish_10pct` (§10.1); `worlds[].weekly_series` and `metrics[].has_weekly_series` (§10.2); retail `discounted_orders` and `customer_base`, wholesale `trade_inventory_build` (§10.3). |

A reader refuses an old set before reading anything else:
`load_world_set(path, min_version=2)` raises `UnsupportedWorldSet` naming
the version found and the version needed. A set newer than the installed
library is always refused the same way, rather than failing on an unknown
field. A reader that does not use `fake_companies` checks `schema_version`
itself.

The ten-seed sets of §9 (`out/worlds/*10`) are version 1. They were built
under the current `moved` rule (§9) but have none of §10's additions; rebuild
them to use those.

### 10.5 Twins aligned before the start, per metric and slice

**A contradiction the runner found, resolved.** On the smoke set White Cube's
root metrics `marketing_spend` and `visit_signup_rate` have
`pre_onset_rms_diff` 0, while §9.5 said no White Cube anomaly was aligned.
The data were right and the spec's wording was wrong: §9.5 counted metric
totals per anomaly, and "no anomaly aligned" meant only that none has *every*
metric aligned. The metrics nearest each root are aligned (the spend ramp's
`marketing_spend`; the lifts' and the CTA regression's `signup_sessions`,
`signups` and `visit_signup_rate`). §9.5's table now lists them per metric,
and §9.7 points to it. No code was wrong.

**A set-level flag, so the runner need not read every twin.** The set's
`effects_summary.parquet` gains, per (regime, anomaly, metric, dimension,
value):

- `aligned_before_start` (boolean): true when in every seed the twins were
  identical on every day before the anomaly's declared start; false when any
  seed's twins differed there; NULL when some seed has no day before the
  start to judge by (a slice value that first occurs later, an anomaly on
  the first day).
- `aligned_before_start_share`: the share of the judged seeds that were
  aligned.

Each twin's `*.effects_summary.parquet` gains `pre_onset_days`, the days
before the start on which the difference is defined; "judged" means it is
above zero. Where the flag is true, `first_diff` is exact and a twin's
difference carries no realization noise from before the anomaly.

"Before the start" and not "before onset": `onset` is when the difference
clears the noise floor, weeks after the start for a ramp. The field names in
the per-twin summary say `pre_onset` for historical reasons; they mean
before the declared start.

### 10.6 Effects by the metric's own aggregation rule

**The problem.** `rel_change` is the relative change of the window mean of
the *daily values*. For a rate that averages daily ratios, which weighs a
quiet day like a busy one; Breakdown's engine takes a rate's window value as
Σnumerator / Σdenominator, so the two numbers differ (on the B2C smoke set,
`payment_failure_rate` under a budget cut: +14% by daily mean, −61% by the
ratio of sums). The same holds for a stock, whose window value by the
manifest's rule is its last day, not its mean.

**Design.** Beside the existing numbers, each twin's
`*.effects_summary.parquet` (every slice) and the ground truth's
`measured_effects` gain:

- `null_aggregated`, `world_aggregated`: the metric's value over the whole
  declared window by its own `time_aggregation` (per-twin summary only);
- `rel_change_aggregated`: `world_aggregated / null_aggregated − 1`.

and the set's `effects_summary.parquet` gains `rel_change_aggregated_mean`,
`_sd`, `_sem`, `_min`, `_max` and `t_across_seeds_aggregated`.

The rule, per `metrics[].time_aggregation`:

| Rule | Window value |
|---|---|
| `sum` | the window total (equals `rel_change` when every day is defined) |
| `end_of_period`, `rolling_window` | the last defined day of the window |
| `ratio_of_sums`, `ratio_of_aggregates`, `average` | each side aggregated by its own rule (`input_time_aggregation`; an average's sum and row count are both sums), then divided; NULL when the denominator is 0 |
| `derived` | `formula` over its inputs' window values, same slice |
| `count_distinct` | NULL: not derivable from days (anything derived from one is NULL too) |

**Which is which.** `rel_change` (and every column without `_aggregated`)
is the window mean of daily values; `*_aggregated` is the metric's own rule.
The verdicts, `moved` and the noise numbers are about `rel_change`; they are
unchanged. Score an attribution size against whichever matches how the tool
aggregates the window. For a rate that is almost always
`rel_change_aggregated`.

Not a schema bump: every field is new, nothing existing changed meaning.

### 10.7 Other windows: measured by the consumer, by a written recipe

**The question.** Effects are summarized for the anomaly's declared window
only. Should the set also summarize a few fixed other windows (the first 28
days, the window plus 90 days), or should the runner measure other windows
itself?

**Decision: the runner measures them, by the recipe below, and nothing more
is summarized.** Reasons:

- The windows a tool needs are its own: the analysis window it was run on,
  which has no fixed relation to the anomaly's dates. Any fixed set the
  generator picks (28 days, +90 days) would still not be that window, and
  each one would add a full copy of the summary's columns.
- A point estimate and its cross-seed statistics over any window need only
  the two twins' series files, which every world has. They are exact and
  cheap, and the recipe is short enough to write down so both sides compute
  the same number. A test (`test_any_window_can_be_measured_from_the_series_as_the_spec_says`)
  implements the recipe independently of `effects.py` from the series files
  and reproduces the set's own `rel_change` and `rel_change_aggregated` on
  the declared window, for every metric of every twin.
- What cannot be recomputed simply, the noise floor, `onset` and `moved`,
  is tied to the declared window by design. A tool scoring a size on
  another window needs the size and its spread across seeds, which the
  recipe gives, not a per-twin significance call.

**The recipe**, for a window from day `a` to day `b` inclusive, one series
(metric, `dimension`, `value`; `dimension` and `value` null for the total):

1. For each single world in `worlds.json` and its `twin_of` null world, read
   both `series` files and keep that series' rows with `a ≤ date ≤ b`.
2. A slice present in only one of the two worlds is filled in the other:
   `metric_value` with `metrics[].fill_nulls_with` (left null when that is
   null), `numerator` and `denominator` with 0.
3. `rel_change` = mean(world `metric_value`) / mean(null `metric_value`) − 1,
   each mean over that world's days where the value is not null. Null when
   the null mean is 0 or undefined.
4. `rel_change_aggregated` = world value / null value − 1, each value by the
   rule of §10.6: `sum` adds the non-null days; `end_of_period` and
   `rolling_window` take the last non-null day; a metric with `has_sides`
   aggregates `numerator` and `denominator` (by `input_time_aggregation` for
   a ratio, both summed for an average) and divides, null when the
   denominator is 0; `derived` evaluates `formula` on its inputs' window
   values of the same slice; `count_distinct` is null (use the weekly file
   for whole weeks).
5. Across the set's seeds, per (anomaly, series): mean, standard deviation
   (n − 1 in the denominator), standard error sd/√n, t = mean/se. To call
   it, compare |t| with `verdicts[].t_critical` (for this seed count), with
   the same `exactly_zero` rule: if no seed's twins differ on any day of the
   timeline, it is exactly zero regardless of the window.

An open-ended anomaly (`end` null) uses the timeline's last day as `b` for
its declared window.

### 10.8 A learned child with several moved parents (assessed, not built)

**The gap.** When one anomaly moves several parents of a node whose edges
are learned, nothing in a set says how much of the child's change came
through each parent, so the runner refuses to score those edges.

**What a split would mean.** "The part of the child's change due to parent
P1" is a path-specific effect: the child as it would be if P1 moved and P2
stayed at its null value. It is defined only relative to the mechanism that
produces the child. The tree's learned edges are a statistical model of
that mechanism, not the mechanism; the generator's mechanism is its entity
builders.

**Case 1: one anomaly moves several parents (the case the runner hits).**
The generator cannot measure a split. An anomaly multiplies one latent
driver, which one entity builder reads; the parents diverge *downstream* of
it, because they are computed from the same rows (a spend cut thins the
sessions table, and paid sessions, signups and trials are all counts over
that one table). A world in which P1 sees the anomaly and P2 does not would
need two different sessions tables in one company: not a coherent
realization. Making it possible means routing the anomaly per consumer of
each table, i.e. rebuilding the entity layer as per-metric structural
equations. That is a rewrite of every vertical's builders, larger than
§9.5's layer 2, and it would still answer for the generator's mechanism
rather than the tree's. The runner's refusal is the right behaviour, and
what can be scored instead is the child's total effect and each parent's
own effect, both of which the sets have.

**Case 2: separate anomalies move separate parents.** Here the split is
well defined and measurable with worlds the generator can already make.
With anomalies A (moving P1) and B (moving P2), the worlds null, only A,
only B and A plus B give, per seed, the exact two-player Shapley split of
the child's change (A's share is the mean of its effect alone and its
marginal effect given B). `generate --anomalies only:A,B` already builds the
fourth world; what is missing is a `worlds` option that plans it (for
example `--combos A+B`) and a summary that writes the split. Cost: one
extra world per pair per seed (2^k − k − 1 for k anomalies), and since
each world adds realization noise (§9.3), the split needs as many seeds as
the effects themselves. Which scripted pairs (if any) hit two parents of
one learned node depends on the tree, so a config that plants such a pair
may be needed too. Not built: the runner's refused edges are case 1, which
this would not help.

### 10.9 What MetricFlow answers at week grain, stated per metric

**The problem.** `metrics[].time_aggregation` says how a metric's days
*should* combine into a week. For some metrics that is not what MetricFlow
returns at `metric_time__week`, and §10.2 left the difference in prose: a
cumulative metric is its last day by the rule and its first day in
MetricFlow. Checking every metric showed more than that.

**What MetricFlow 0.13 does**, verified metric by metric on all four smoke
configs (`_check_week_rules` in the MetricFlow test reproduces `mf query
--group-by metric_time__week` for every metric from the daily series by the
stated rule, on every full week):

| `metricflow_week` | Metrics | Agrees with `time_aggregation`? |
|---|---|---|
| `sum_of_days` | every additive `sum`/`count` measure | yes |
| `last_day` | since §10.10: the snapshot stocks `mrr`, `open_deals`, `open_pipeline_value` (semi-additive measures) and the cumulative `wau`, `customer_base`, `trade_inventory_build` (`period_agg: last`) | yes |
| `average_of_rows` | averages | yes |
| `distinct_over_week` | `dau`, `paying_active_users`, `active_subscriptions` | no (the number is in the weekly series file) |
| `first_day` | a cumulative metric with MetricFlow's default `period_agg` (none since §10.10) | no (the rule takes the last day) |
| `ratio_of_week_inputs`, `derived_of_week_inputs` | ratios and derived metrics, from their inputs' week values | when every input agrees (not for `customer_churn_rate`, `arpu`) |

**Design.** Two fields on every `metrics[]` entry, so the mismatch is named
rather than left in prose:

- `metricflow_week`: the rule above;
- `matches_metricflow_week`: whether `time_aggregation` applied to the
  daily series gives MetricFlow's week number;
- `input_metricflow_week` (ratios and derived metrics): each input's own
  `metricflow_week` (below). The first two fields are on every metric. False for 11 metrics across
  the four verticals when this was written; since §10.10 for 5, all
  distinct counts or built on one: `active_subscriptions`, `dau`,
  `paying_active_users`, `arpu`, `customer_churn_rate`.

`time_aggregation` is **not** changed to match MetricFlow. Where they differ,
MetricFlow's number is the wrong business number (seven days of MRR added
up; a weekly WAU that is mostly the previous week), and the rule is what a
tree should use. A consumer that must reproduce `mf query` uses
`metricflow_week`; one that wants the period's value uses `time_aggregation`;
none may assume the two agree where `matches_metricflow_week` is false.

**Each side of a ratio, too.** `customer_churn_rate` divides churned
subscriptions by active subscriptions. `input_time_aggregation` says the
denominator is `end_of_period` (the last day's count), while MetricFlow's
weekly rate divides by the week's *distinct* active subscriptions, which the
weekly series file serves; the runner measured that about 0.7% above the
last-day count. So every ratio and derived metric also carries
`input_metricflow_week`: for each key of `inputs`, that input's own
`metricflow_week` (`{"numerator": "sum_of_days", "denominator":
"distinct_over_week"}` here). `distinct_over_week` on a side means: read that
side from the weekly series file. A reader reproducing MetricFlow builds
each side by it and never has to infer which file holds the side.
`input_time_aggregation` stays the business rule.

**Not answered: month grain.** MetricFlow's monthly distinct counts (and so
the monthly churn rate's denominator) are not in any file; only weeks are.

**Not a schema bump.** The fields are new and `time_aggregation` keeps its
meaning, so a version 2 reader that ignores them reads every value as
before. (A reader that validates the manifest strictly, as
`load_world_set` does with an older fake_companies, rejects the unknown
fields; upgrade the library to read new sets.)

### 10.10 Stocks MetricFlow answers by their last day (2026-11-24)

**The problem.** Breakdown's C66 (its PR #152) found that MetricFlow summed
the daily snapshots of a stock at a coarser grain: White Cube `mrr` at week
grain came back as 186,383 against a last-day level of 26,946, and Meridian's
weekly `open_deals` and `open_pipeline_value` read 7.2 to 8.1 times their
week-end level. Every MetricFlow consumer got that number. Breakdown now
refuses such a stock at load unless the measure is declared semi-additive.

**Audit of every measure** over a table with one row per entity per day,
and of every running total:

| Vertical | Measure (metric) | Table | Over time | Why |
|---|---|---|---|---|
| b2c_saas | `mrr` | `fct_subscription_days` | snapshot | MRR on the day; a week holds one level, not seven |
| b2c_saas | `subscription_day_count` (`member_activity_rate` denominator) | `fct_subscription_days` | additive | member-days: the rate is active member-days over member-days, both sums |
| b2c_saas | `active_subscriptions` | `fct_subscription_days` | `count_distinct`, unchanged | a week's value is "distinct during the week" (weekly series file); C66 accepts it |
| b2c_saas | `active_users`, `paying_active_users` | `fct_activity_days` | `count_distinct`, unchanged | as above |
| b2c_saas | `paying_user_days` | `fct_activity_days` | additive | paying user-days, the activity-rate numerator |
| b2b_services | `open_deals`, `open_pipeline_value` | `fct_deal_days` | snapshot | deals open on the day |
| retail_dtc | `customer_base` (cumulative over `new_customers`) | `dim_customers` | measure additive; metric a level | `period_agg: last` |
| cpg_wholesale | `trade_inventory_build` (cumulative over `net_inventory_units`) | `fct_inventory_flows` | measure additive (flows in and out); metric a level | `period_agg: last` |
| b2c_saas | `wau` (cumulative, 7-day window) | `fct_activity_days` | trailing window | `period_agg: last`: the week's WAU is the week itself |

Every other measure is over an event table (one row per session, order,
event, invoice, shipment, scan) and is additive.

**Design.** The three snapshot measures declare
`non_additive_dimension: {name: date_day, window_choice: max}`: the period
takes the rows of its last day. No `window_groupings`: one snapshot per day
covers every entity, so the last day of the period is the same for all of
them (with groupings MetricFlow would take each entity's own last day, which
would count a deal closed mid-week as still open). The three cumulative
metrics declare `cumulative_type_params: {period_agg: last}`. `semantic.py`
reads both (it refused `non_additive_dimension` before), and
`metricflow_week` says `last_day` for all six.

**What changed in a set.** No raw table, no ground truth, no daily or weekly
series value: a day is a day either way. In `worlds.json`, `mrr`,
`open_deals`, `open_pipeline_value`, `wau`, `customer_base` and
`trade_inventory_build` go from `sum_of_days` or `first_day` to `last_day`
and `matches_metricflow_week` from false to true; `arpu`'s
`input_metricflow_week` names `mrr` as `last_day`. `arpu` still does not
match: MetricFlow divides the last day's MRR by the week's distinct
subscriptions (about 0.7% more than the last day's count), where it used to
divide seven days of MRR by them, seven times too high. Not a schema bump: no
field changed meaning, and a set built before this says what MetricFlow
answered then.

**Verified.** `tests/test_semantic_matches_metricflow.py` reproduces `mf
query` at week grain for every metric from the daily series by
`metricflow_week` and checks `matches_metricflow_week` against the numbers;
for the snapshot measures it also checks week and month, in total and by
every dimension, against the period's last day.

---

*This document is written and maintained by an AI agent (Claude), with human oversight.*
