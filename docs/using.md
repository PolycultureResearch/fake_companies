# Using fake_companies (recipe guide)

A task-oriented guide for **using** the generator to spin up a company dataset —
for testing a detector, demoing an analytics tool, or producing a fixture. If you
are *modifying* the generator, read `AGENTS.md` and `docs/plan.md` instead.

The config YAML **is the API**. You describe a company (trends, seasonality,
scale) and inject anomalies; the generator produces deterministic raw data plus
`ground_truth.json`. Start from a canonical scenario and edit it.

## Quickstart

```bash
uv sync
# generate from an existing scenario
uv run fake-companies generate --config configs/white_cube_b2c_app.yaml --out out/white_cube.duckdb
uv run fake-companies truth --db out/white_cube.duckdb          # what anomalies were injected
```

- `configs/reference_b2c_saas.yaml` — canonical 2-year B2C SaaS reference scenario (~1.3M sessions,
  ~50k users, ~7.5M product events). Full run ≈ 20s.
- `configs/smoke_90d.yaml` — 90 days, small, seconds to run. Use for fast loops/CI.

To iterate on your own scenario, **copy** a config and edit the copy:
`cp configs/reference_b2c_saas.yaml configs/my_scenario.yaml`.

## Recipes

Each recipe is a block you add under `anomalies.scripted:` in your config. Every
scripted (and `surprise`) anomaly is recorded in `ground_truth.json` and
`meta.ground_truth`; baseline trends/seasonality are **not** anomalies.

### A detectable revenue drop (for Breakdown RCA / Tremor KPI)

Cut a driver so the effect cascades downstream (spend → sessions → signups →
trials → MRR):

```yaml
- name: paid_search_budget_cut
  kind: rate
  type: level_shift
  target: spend.paid_search   # a driver; see "Drivers" below
  window: { start: 2025-03-01, end: 2025-03-21 }
  magnitude: 0.6              # multiply the rate by 0.6 (-40%) over the window
```

Or hit revenue directly with a churn spike:

```yaml
- name: q2_churn_spike
  kind: rate
  type: level_shift
  target: churn.pro
  window: { start: 2025-06-01, end: 2025-06-30 }
  magnitude: 1.8             # +80% churn among pro subscribers
```

### A gradual slope break (trend change)

```yaml
- name: seo_slowdown
  kind: rate
  type: trend_change
  target: sessions.organic
  window: { start: 2025-01-06 }   # persistent from here on
  magnitude: 0.9993               # compounding ~-0.07%/day
```

### A segmented regression (for dimensional RCA)

Confine an effect to a slice — invisible in the topline until you group by that
dimension (ideal for Breakdown's dimensional attribution):

```yaml
- name: mobile_us_conversion_drop
  kind: rate
  type: level_shift
  target: signup_rate.paid_social
  window: { start: 2025-02-10, end: 2025-03-12 }
  magnitude: 0.70
  segment: { country: US, device: mobile }   # dims: country | device | channel | plan
```

### A data pipeline problem (for Tremor dataflow)

These mutate *observed* rows only (business truth unchanged):

```yaml
- name: product_pipeline_outage       # volume collapse
  kind: dq
  type: volume_dropout
  target: product.events
  window: { start: 2025-04-15, end: 2025-04-16 }
  magnitude: 0.3                       # only 30% of rows land

- name: currency_mix_shift            # PSI shift on a categorical
  kind: dq
  type: distribution_shift
  target: billing.payments
  window: { start: 2025-10-01, end: 2025-10-10 }
  magnitude: 1.0
  params: { column: currency, new_mix: { USD: 0.5, EUR: 0.5 } }

- name: country_null_spike            # a column starts going null
  kind: dq
  type: null_spike
  target: web.sessions
  window: { start: 2025-08-05, end: 2025-08-07 }
  magnitude: 0.25
  params: { column: country }
```

Other dq types: `loading_delay` (freshness; `magnitude` = lag multiplier),
`duplicate_rows` (`magnitude` = fraction duplicated).

### A blind test (score a detector without cheating)

Let the generator sample its own hidden anomalies, run your detector, then score:

```yaml
anomalies:
  surprise:
    count: 5
    kinds: [rate, dq]
    magnitude: { min: 0.5, max: 1.8 }
    min_gap_days: 21
```

```bash
uv run fake-companies generate --config configs/my_scenario.yaml --out out/x.duckdb
# ... run your detector, collect its events as JSON (list of {detected_at, metric}) ...
uv run fake-companies score --truth ground_truth.json --events my_events.json --tolerance 3
```

Don't open `ground_truth.json` until after scoring.

### A company with no anomalies, or with exactly one

```bash
uv run fake-companies generate -c configs/alpenglow_retail_dtc.yaml -o out/null.duckdb \
    --anomalies none                         # no scripted anomalies, no surprises
uv run fake-companies generate -c configs/alpenglow_retail_dtc.yaml -o out/one.duckdb \
    --anomalies only:paid_social_budget_cut  # named scripted anomalies, no surprises
```

`--anomalies all` is the default and is what the config says. Selection never
changes any other random draw: the rows that the dropped anomalies did not touch
come out the same.

### Change one thing about the baseline

`--set` overrides a config value by dotted path (repeatable; the value is YAML;
`*` stands for every key at that point). The result is validated like a
hand-edited file and the overrides are recorded in `run_manifest.json`.

```bash
uv run fake-companies generate -c configs/smoke_retail_90d.yaml -o out/x.duckdb \
    --set calendar.annual_amp=0 --set 'traffic.channels.*.growth={kind: flat}'
```

`--regime <name>` applies a named set of overrides from
`configs/regimes/<vertical>.yaml`: `flat`, `declining`, `accelerating`,
`long_history` (three years from the configured start) or `no_seasonality` (no
annual cycle, holiday effect or day-of-week shape). A regime changes the
baseline only and never the anomalies. In `cpg_wholesale` the three growth
regimes move marketing spend and nothing else, because that is the only driver
there with a growth curve.

### Change scale / timeline / seed

- Timeline: `timeline: { start: 2024-01-01, days: 365 }` (or `end:`).
- Seed: top-level `seed:` — same seed + config ⇒ byte-identical output.
- Scale: traffic `channels.*.baseline` / `spend_baseline`, and `funnel.signup_rate`
  drive user/session volume; `engagement.dau_over_active` × `events_per_active_day`
  drive product-event volume.

## Feeding the consumers

- **Breakdown / Tremor KPI mode** need modeled daily metrics. Build the dbt project:
  ```bash
  uv sync --extra dbt
  export DBT_PROFILES_DIR=$PWD/dbt/b2c_saas FAKE_DB=$PWD/out/white_cube.duckdb
  uv run dbt build --project-dir dbt
  uv run mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv   # in dbt/
  ```
  `examples/breakdown_white_cube_tree.yml` is a ready metric tree. `scripts/verify_consumers.py`
  checks every tree metric + Tremor table against a built db.
- **Tremor dataflow mode** profiles the raw tables directly (needs `event_time` +
  `_loaded_at`). See `examples/tremor_white_cube.yaml`.

## World sets (evaluating a tool, not just running it)

One generated company cannot tell you whether a tool is right. A **world set**
is a batch of related companies with a manifest: the same company with and
without each anomaly, across seeds, optionally under several regimes.

```bash
uv run fake-companies worlds --config configs/alpenglow_retail_dtc.yaml \
    --seeds 0-9 --regime baseline --regime flat \
    --null --singles all --format parquet --jobs 4 --out out/worlds/alpenglow
```

Per regime and seed this writes the **null** world (no anomalies), one
**single** world per scripted anomaly (`--singles all`, or a comma list of
names; singles always bring their null twin), and with `--story` the config as
written. `--format duckdb` (default) writes one database per world; `parquet`
writes the raw tables as `<schema>.<table>.parquet`, which is smaller. `--jobs`
only changes the wall time. `--set` applies an override to every world.
`--no-raw` skips the raw tables altogether (they are most of a world's size)
for a consumer that reads only the metric series and effects; `--no-series`
and `--no-effects` skip those.

```
out/worlds/alpenglow/
  worlds.json                                   # the manifest: read this, not the tree
  effects_summary.parquet                       # every effect across seeds
  baseline/s0/null/                             # or null.duckdb
  baseline/s0/null.ground_truth.json            # []
  baseline/s0/null.run_manifest.json
  baseline/s0/null.series.parquet               # every world has one
  baseline/s0/null.weekly_series.parquet        # B2C only: distinct counts by week
  baseline/s0/only.paid_social_budget_cut/
  baseline/s0/only.paid_social_budget_cut.ground_truth.json
  baseline/s0/only.paid_social_budget_cut.run_manifest.json
  baseline/s0/only.paid_social_budget_cut.series.parquet
  baseline/s0/only.paid_social_budget_cut.effects.parquet
```

Read a set through its manifest (paths are relative to it):

```python
import json, pandas as pd
from fake_companies.worlds import load_world_set

root = "out/worlds/alpenglow"
ws = load_world_set(root)                      # a validated WorldSetManifest
for single, null in ws.twins():                # (single world, its null twin)
    truth = json.load(open(f"{root}/{single.ground_truth}"))[0]
    effects = pd.read_parquet(f"{root}/{single.effects}")
    orders = pd.read_parquet(f"{root}/{single.path}/shop_db.orders.parquet")
summary = pd.read_parquet(f"{root}/{ws.effects_summary}")
```

World ids are `<slug>.<regime>.s<seed>.<null | only.<anomaly> | story>`. The
same config, seed, regime and anomaly always give the same id and the same
bytes, whichever other worlds are in the set.

**Ground truth in a single world** has the usual fields plus:

- `root_metric`, `root_dimension`, `root_value` (and `root_slice`, the same as
  a mapping): the MetricFlow metric the anomaly moves first and the slice of it
  that it touches, e.g. `conversion_rate` / `channel,device` /
  `paid_search,mobile`. `worlds.json` → `metrics[].dimensions` maps each
  dimension to its MetricFlow group-by id (`channel` → `session__channel`).
  Every `generate` run writes these too.
- `measured_effects`: per metric, read off the difference between this world
  and its null twin: `rel_change` over the declared window, `noise_floor`,
  `first_diff` (the first day the twins differ at all), `onset` / `offset`
  (first and last day over the floor), `days_over_floor`, `z_window` /
  `z_extended` (the window test) and `moved`.
- `root_metric_rate`: when the root is a rate's numerator count (a
  `return_rate.<category>` anomaly roots at `returned_units`, a `churn.<plan>`
  anomaly at `churned_subscriptions`, because MetricFlow cannot slice those
  ratios), the rate itself (`return_rate`, `customer_churn_rate`). A tree whose
  node is the rate matches on this. Null otherwise. Written by every run.
- `metrics_moved`: every metric whose total moved, declared or not. `[]` means
  the anomaly moved no metric — true of most data-quality anomalies, whose
  truth is in `affected_signals`.
- `root_effect` and `root_metric_confirmed`: the same for the root metric at
  the root slice.
- `declared_affected_metrics_confirmed`: each of `affected_metrics` under
  `moved`, `moved_in_slice_only` or `not_detected`.

**`*.effects.parquet`** is the day-by-day table behind those numbers: `date`,
`metric`, `dimension`, `value`, `null_value`, `world_value`, `diff`, for every
metric of the vertical (`dimension` and `value` are null for the total; the
slices are the anomaly's root slice and each of its dimensions).

**Which date to use.** `first_diff` is the first day the twins differ at all.
When that series' `pre_onset_rms_diff` is 0 the twins were identical until the
anomaly, so `first_diff` is the exact day the effect arrives: prefer it. `onset`
is the first day the difference clears the noise floor, which is when a
detector could first see it — for a ramp or a trend change that is by design
two to three weeks after the start, and for a sparse metric it may never
happen (`onset` null). Use `onset` only when `pre_onset_rms_diff` is not 0
(then `first_diff` is just the first noisy day).

**`*.effects_summary.parquet`** (single worlds) has those same stats for every
row of the effects table: the total of every metric, every single-dimension
slice in the series file, and the anomaly's root slice. The ground truth's
`measured_effects` is its totals only; a node bound to a slice (paid sessions =
`sessions` where `channel` is `paid_search`) looks itself up here by `metric`,
`dimension`, `value`.

**One verdict per anomaly and metric.** A twin's `moved` is one seed's opinion
and it flips between seeds for small effects. `effects_summary.parquet` at the
set root has a `verdict` per regime, anomaly, metric and slice:
`established_up` / `established_down` (the mean across seeds is far enough from
zero by the seeds' own spread, two-sided 1%), `open`, `exactly_zero` (no seed's
twins ever differ) or `undefined`. Choose targets from it, not from one world.
A small set (three seeds) can rarely reach the 1% bar (|t| ≥ 9.9), so the same
file also has `verdict_10pct`, the same test at two-sided 10% (|t| ≥ 2.9 at
three seeds), which establishes about one in ten effects that do not exist:
use it only when the strict one cannot answer, and say so.
`seeds_to_establish` / `seeds_to_establish_10pct` project how many seeds each
row would need at each level. `worlds.json` → `verdicts` lists every verdict
column with its `level`, `t_critical` at this set's seed count and `min_seeds`.

**`*.series.parquet`** (every world: null, single, story) is the daily series
of every metric, as MetricFlow would return it, so a consumer needs neither dbt
nor the raw tables: `date`, `metric`, `dimension`, `value`, `metric_value`,
`numerator`, `denominator`. It holds the total (`dimension` and `value` null)
and one slice per dimension listed for the metric in `worlds.json` →
`metrics[].dimensions`. Every timeline day is present; `metric_value` is null
where the metric is undefined (a ratio over zero). `numerator` / `denominator`
are filled where `metrics[].has_sides` is true: ratio metrics (their two input
metrics) and averages (the sum and the row count). `metrics[].time_aggregation`
says how each metric combines over time:

- `sum`; `end_of_period` (a stock: MRR, open pipeline — take the last day);
- `ratio_of_sums`: sum `numerator` and `denominator`, then divide (never
  average daily ratios); `average` works the same way from its sides;
- `ratio_of_aggregates`: a ratio with a side that is not a sum.
  `customer_churn_rate` divides churned subscriptions (a flow) by active
  subscriptions (a stock); aggregate each side by
  `metrics[].input_time_aggregation` and then divide;
- `derived`: aggregate each input by `input_time_aggregation`, then evaluate
  `formula`, a tree of `{"metric"}`, `{"const"}` and `{"op", "args"}` nodes
  (`+ - * /`, `neg`, `nullif_zero`); `expr` is the same thing as SQL text;
- `count_distinct` (DAU): not derivable for a longer period from the days;
  the week is in the weekly series file (below). `rolling_window` (WAU): a
  trailing window already, take the last day.

`metrics[].measure_agg` is the measure's own aggregation. A stock with
`measure_agg: count_distinct` (`active_subscriptions`) is the distinct count
on each day; its end-of-week value is the last day's count, which is not what
MetricFlow returns at week grain (distinct over the whole week).

**`*.weekly_series.parquet`** answers those numbers. For every metric with
`metrics[].has_weekly_series` (the simple `count_distinct` ones: `dau`,
`paying_active_users`, `active_subscriptions` in B2C; other verticals have
none and their worlds have no such file) it holds MetricFlow's
`metric_time__week` value: `date` (the Monday starting the week), `metric`,
`dimension`, `value`, `metric_value`, and `days`, the timeline days in that
week (under 7 only at the timeline's two edges, where the count covers the
days that exist). Totals and every single-dimension slice. The path is
`worlds[].weekly_series`.

A cumulative metric (`wau`, `rolling_window`) is a trailing window already;
over a week take the last day. MetricFlow 0.13 instead returns its value on
the week's *first* day; both are in the daily series.

**Read the measured numbers as effect plus noise.** A single world and its null
twin share a seed, but most entity builders draw for the whole timeline from one
stream, so the two realizations differ beyond the anomaly, often from the first
day. One twin cannot confirm a small effect: `not_detected` means "did not clear
this twin's noise", not "the generator left it alone". For the expected effect
use `effects_summary.parquet` (`rel_change_mean`, `rel_change_sd`,
`rel_change_sem`, `moved_share`, per regime, anomaly, metric and slice) and
build more seeds until the standard error is small enough for your purpose.
`fake_companies.effects` documents every number.

The metric series are computed in-process from the vertical's dbt project
(`fake_companies.semantic`), not by running dbt, so building a set needs the
repo checkout but not the dbt extra. `--no-effects` skips them.

## Reference

**Rate anomaly types:** `spike`, `drop`, `level_shift`, `trend_change`, `ramp`,
`seasonality_change`. **DQ types:** `volume_dropout`, `null_spike`,
`distribution_shift`, `loading_delay`, `duplicate_rows`.

**Drivers** (rate-anomaly `target`s) — dump the panel to inspect them:
`fake-companies generate ... --dump-drivers out/drivers.csv`:
- `spend.<paid_channel>`, `sessions.<organic_or_fixed_channel>`
- `signup_rate.<channel>`, `trial_start_rate`, `trial_convert`
- `churn.<plan>`, `upgrade`, `downgrade`, `resurrect`
- `dau_over_active.<plan>`, `events_per_active_day.<plan>`

**DQ `target`s** are raw table names: `web.sessions`, `product.events`,
`billing.payments`, `billing.invoices`, `app_db.users`, `ad_platform.ad_spend`.

**Metrics** (daily via MetricFlow): `marketing_spend`, `sessions`, `signups`,
`signup_sessions`, `visit_signup_rate`, `trials_started`, `trial_conversion_rate`,
`new_subscriptions`, `churned_subscriptions`, `active_subscriptions`, `mrr`,
`new_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr`, `arpu`,
`customer_churn_rate`, `dau`, `wau`, `product_events`, `revenue`,
`payment_failure_rate`. Dimensions where grain allows: `plan`, `signup_channel`,
`country`, `device`.

> Current scope is B2C SaaS (freemium + trial → subscription). Plan tiers,
> channels, and countries are config-driven, but the entity model (sessions →
> signups → subscriptions → billing → usage) is fixed. Other verticals
> (e-commerce, B2B) are on the roadmap.
