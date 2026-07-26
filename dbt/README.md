# fake_companies dbt project

Companion dbt-duckdb project that models the raw source tables the generator
writes (`ad_platform`, `web`, `app_db`, `billing`, `product`) into a
staging -> marts -> MetricFlow semantic layer. Consumers (Breakdown, Tremor KPI
mode) fetch daily metric series via
`mf query --metrics <m> --group-by metric_time__day --csv`.

## Layout

- `models/staging/` — one `stg_<table>` view per raw table (light rename/cast,
  deduplicated by PK, `_loaded_at` exposed). `sources.yml` declares every raw
  source with `_loaded_at` freshness.
- `models/marts/` — dimension/fact tables: `dim_users`, `dim_plans`,
  `fct_sessions`, `fct_signups`, `fct_trials`, `fct_mrr_movements`,
  `fct_subscription_days`, `fct_invoices`, `fct_payments`, `fct_activity_days`,
  plus `metricflow_time_spine`.
- `models/semantic/` — MetricFlow semantic models (`semantic_models.yml`) and
  metrics (`metrics.yml`).
- `tests/assert_subscription_days_reconciles.sql` — singular reconciliation
  test: `fct_subscription_days` active-subs / MRR must match an independent
  raw-SQL recomputation for a sample of days.

## Point dbt at a database

The DuckDB profile reads the `FAKE_DB` env var (default `../out/acme.duckdb`).
dbt materializes its models into `main_staging` / `main_marts` schemas inside
that same DuckDB file. Generate a database first:

```bash
# from the repo root
uv sync --extra dbt
.venv/bin/fake-companies generate --config configs/smoke_90d.yaml --out out/smoke.duckdb
# or the full 2-year scenario:
.venv/bin/fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb
```

## Build + test (M5 gate)

```bash
# from the repo root
DBT_PROFILES_DIR=$(pwd)/dbt FAKE_DB=$(pwd)/out/smoke.duckdb \
  .venv/bin/dbt build --project-dir dbt
```

All models + tests should be green (includes the `fct_subscription_days`
reconciliation test).

## Semantic layer (M6 gate)

MetricFlow reads the compiled manifest, so `dbt parse` (or `dbt build`) must run
after any YAML edit. Run `mf` from inside `dbt/`:

```bash
cd dbt
export DBT_PROFILES_DIR=$(pwd)
export FAKE_DB=$(cd .. && pwd)/out/smoke.duckdb

../.venv/bin/dbt parse
../.venv/bin/mf validate-configs

# Breakdown's exact fetch path — daily series for one metric:
../.venv/bin/mf query --metrics mrr --group-by metric_time__day \
  --start-time 2024-01-01 --end-time 2024-03-30 --csv /tmp/mrr.csv
```

## Metrics

`marketing_spend`, `sessions`, `signups`, `visit_signup_rate`, `trials_started`,
`trial_conversion_rate`, `new_subscriptions`, `churned_subscriptions`,
`active_subscriptions`, `mrr`, `new_mrr`, `expansion_mrr`, `contraction_mrr`,
`churned_mrr`, `arpu`, `customer_churn_rate`, `dau`, `wau`, `product_events`,
`revenue`, `payment_failure_rate`.

Common dimensions (where the grain allows): `plan`, `signup_channel`, `country`,
`device`, `channel`, `category`, `payment_method`, `status`.

### MRR movement semantics

MRR uses monthly-equivalent price (annual plans contribute `price / 12`).
`fct_mrr_movements` buckets subscription events: `trial_convert`/`resurrect` ->
new, `upgrade` -> expansion, `downgrade` -> contraction, `cancel` -> churned.
`fct_subscription_days` reconstructs each subscription's active plan over time
from the event stream (the raw `subscriptions.plan_id` is only the final plan)
and explodes it across the time spine, one row per active subscription per day.
