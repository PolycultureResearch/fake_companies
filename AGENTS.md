# fake_companies

Generator of realistic fake raw data for companies, used to test, develop, and demo
analytics software — primarily Devon's packages **Breakdown**
(/Users/devon/Documents/code/breakdown, Bayesian metric-tree root-cause analysis) and
**Tremor** (/Users/devon/Documents/code/tremor, anomaly detection). It replaces real
client data in demos and provides ground-truth-labeled anomalies for detector testing.

Full spec: `docs/plan.md`. Read it before nontrivial changes.

## What this produces

`fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb`
writes raw source tables (schemas: ad_platform, web, app_db, billing, product) plus
`meta.ground_truth` / `meta.run_manifest`, and optionally Parquet/CSV exports. The
companion dbt project in `dbt/` (dbt-duckdb) models them: staging → marts → MetricFlow
semantic layer. Consumers fetch daily metric series via
`mf query --metrics <m> --group-by metric_time__day --csv` (Breakdown's exact path;
Tremor KPI mode too). Tremor dataflow mode profiles the raw tables directly (needs
event_time + `_loaded_at` columns).

## Architecture (three layers — keep them separate)

1. `src/fake_companies/latent/` — config-driven daily rate panel per (driver, segment):
   baseline × growth curve × weekly/annual/holiday seasonality × AR(1) lognormal noise
   × anomaly multipliers. **Rate anomalies are applied here** so effects cascade
   causally downstream (signup-rate drop → fewer trials → less MRR).
2. `src/fake_companies/entities/` — entity-level simulation drawn stochastically from
   the latent rates (Poisson sessions, binomial signups, hazard-based subscription
   lifecycles, NHPP usage events, billing). Never write aggregates directly — aggregate
   realism must emerge from raw rows.
3. `src/fake_companies/corruption/` — observation layer: `_loaded_at` connector models
   and data-quality corruptions (volume_dropout, null_spike, distribution_shift,
   loading_delay, duplicate_rows). Business truth unchanged; only observed rows mutate.

Every injected anomaly (rate or dq, scripted or surprise-sampled) emits a
`GroundTruthRecord` — this is the scoring key; never let an injection skip it.

## Hard rules

- **Determinism**: all randomness flows from the scenario `seed` through `RngHub`
  named streams (`core/rng.py`). Never call `np.random.*` module functions or seed ad
  hoc. Same seed + config ⇒ byte-identical output.
- **Vectorize**: numpy/pandas array ops → arrow → DuckDB. No per-user/per-day Python
  loops; target < ~60 s for the 2-year default scenario.
- Config is the API: every behavior knob lives in the YAML scenario, mirrored 1:1 by
  pydantic models in `config/schema.py`. No hidden constants for tunable behavior.
- Baseline shape (growth/seasonality/noise) is NOT an anomaly and never enters ground
  truth; only `anomalies:` entries do.
- Tremor is early-stage and volatile: build against the stable contract only (raw
  tables + daily MetricFlow KPIs); keep Tremor-specific bits in `examples/`.

## Commands

- `uv sync` — install (Python pinned in `.python-version`, latest stable)
- `uv run fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb`
- `uv run pytest` (`-m "not slow"` for quick loop; slow = statistical suite)
- `uv run ruff check . && uv run ruff format .` (line length 100)
- dbt: `cd dbt && dbt build` (profile reads `FAKE_DB` env var, default `../out/acme.duckdb`)
- Semantic layer: `mf validate-configs`, then
  `mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv`

## Testing conventions

Statistical tests assert tolerances (Poisson/binomial CIs), never exact values.
Determinism tests compare parquet hashes across two runs. Ground-truth honesty tests
recompute each injected anomaly's affected aggregate and require ≥ ~3 robust sigmas
vs clean windows. Use the small 90-day smoke config in CI/quick loops.

## Style

Match breakdown/tremor house style: ruff, line length 100, pydantic 2, typer CLI,
`src/` layout, uv-managed. Python: latest stable.
