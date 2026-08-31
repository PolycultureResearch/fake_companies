# fake_companies

Generator of realistic fake raw data for companies, used to test, develop, and demo
analytics software — primarily Devon's packages **Breakdown**
(/Users/devon/Documents/code/breakdown, Bayesian metric-tree root-cause analysis) and
**Tremor** (/Users/devon/Documents/code/tremor, anomaly detection). It replaces real
client data in demos and provides ground-truth-labeled anomalies for detector testing.

Full spec: `docs/plan.md` (original B2C SaaS build) and `docs/roadmap.md` (vertical
split). Read them before nontrivial changes.

## What this produces

`fake-companies generate --config configs/<scenario>.yaml --out out/<db>.duckdb`
writes raw source tables plus `meta.ground_truth` / `meta.run_manifest`, and
optionally Parquet/CSV exports. Which tables depends on the scenario's **vertical**
(`company.vertical` in the YAML): `b2c_saas` (acme, white_cube — schemas ad_platform,
web, app_db, billing, product) or `retail_dtc` (alpenglow — ad_platform, web, shop_db,
fulfillment, payments). The companion dbt project in `dbt/<vertical>/` (dbt-duckdb)
models them: staging → marts → MetricFlow semantic layer. Consumers fetch daily metric
series via `mf query --metrics <m> --group-by metric_time__day --csv` (Breakdown's
exact path; Tremor KPI mode too) — the one contract identical across verticals. Tremor
dataflow mode profiles the raw tables directly (needs event_time + `_loaded_at`
columns).

## Architecture (generic engine × vertical plug-ins)

The pipeline has three layers; the *mechanisms* are generic, the *business model* is a
vertical package implementing the `Vertical` protocol (`verticals/base.py`, resolved
from `company.vertical` by the registry in `verticals/__init__.py`):

1. `src/fake_companies/latent/` — daily rate panel per (driver, segment): baseline ×
   growth curve × weekly/annual/holiday seasonality × AR(1) lognormal noise × anomaly
   multipliers. **Rate anomalies are applied here** so effects cascade causally
   downstream (spend cut → fewer sessions → fewer orders/signups → less revenue).
   The driver *catalog* (which drivers exist) is vertical-owned
   (`verticals/<name>/drivers.py`).
2. Entity simulation — raw rows drawn stochastically from the latent rates, all
   vectorized. Vertical-owned (`verticals/<name>/entities/`): b2c_saas does
   sessions→signups→trials→subscriptions→billing→usage; retail_dtc does
   sessions→orders→baskets→shipments→returns→payments. Shared builders any web
   vertical reuses (ad_spend, sessions) live in `src/fake_companies/shared/`.
   Never write aggregates directly — aggregate realism must emerge from raw rows
   (e.g. AOV is not a driver; it emerges from baskets × prices).
3. `src/fake_companies/corruption/` — observation layer: `_loaded_at` connector models
   and data-quality corruptions (volume_dropout, null_spike, distribution_shift,
   loading_delay, duplicate_rows), generic over any vertical's `tables()`. Business
   truth unchanged; only observed rows mutate.

A vertical package owns: `config.py` (a `BaseScenarioConfig` subclass — the YAML stays
flat), `tables.py` (TableSpecs; list order = loading order = RNG draw order),
`drivers.py`, `entities/`, `dq.py` (columns dq events may corrupt), `metrics.py`
(driver → affected MetricFlow metrics), and a `dbt/<vertical>/` project.
`tests/test_vertical_conformance.py` keeps all of these consistent — run it after
touching any of them.

Every injected anomaly (rate or dq, scripted or surprise-sampled) emits a
`GroundTruthRecord` — this is the scoring key; never let an injection skip it.

## Hard rules

- **Determinism**: all randomness flows from the scenario `seed` through `RngHub`
  named streams (`core/rng.py`). Never call `np.random.*` module functions or seed ad
  hoc. Same seed + config ⇒ byte-identical output. Never rename an existing stream
  literal or reorder draws within one — `tests/test_golden_migration.py` pins the
  B2C SaaS outputs.
- **Vectorize**: numpy/pandas array ops → arrow → DuckDB. No per-user/per-day Python
  loops; target < ~60 s for a 2-year scenario.
- Config is the API: every behavior knob lives in the YAML scenario, mirrored 1:1 by
  pydantic models (`config/schema.py` envelope + `verticals/<name>/config.py`
  sections). No hidden constants for tunable behavior.
- Baseline shape (growth/seasonality/noise) is NOT an anomaly and never enters ground
  truth; only `anomalies:` entries do.
- Tremor is early-stage and volatile: build against the stable contract only (raw
  tables + daily MetricFlow KPIs); keep Tremor-specific bits in `examples/`.

## Commands

- `uv sync` — install (Python pinned in `.python-version`, latest stable). If imports
  of `fake_companies` break after a sync, `uv sync --reinstall-package fake-companies`
  (the editable install is flaky on this machine; pytest imports via `pythonpath=src`
  and is immune).
- `uv run fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb`
- `uv run fake-companies generate --config configs/alpenglow_retail_dtc.yaml --out out/alpenglow.duckdb`
- `uv run pytest` (`-m "not slow"` for quick loop; slow = statistical + golden suite)
- `uv run ruff check . && uv run ruff format .` (line length 100)
- dbt: `cd dbt/<vertical> && dbt build` (profile reads `FAKE_DB` env var; defaults are
  `../../out/acme.duckdb` for b2c_saas, `../../out/alpenglow.duckdb` for retail_dtc)
- Semantic layer: `mf validate-configs`, then e.g.
  `mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv` (b2c_saas)
  or `--metrics net_revenue` (retail_dtc)
- Consumer contracts: `FAKE_DB=out/<db>.duckdb python scripts/verify_consumers.py`
  (picks the dbt project + examples from the database's manifest vertical)

## Testing conventions

Statistical tests assert tolerances (Poisson/binomial CIs), never exact values.
Determinism tests compare parquet hashes across two runs (both smoke configs).
Ground-truth honesty tests recompute each injected anomaly's affected aggregate and
require ≥ ~3 robust sigmas vs clean windows (smoke configs run anomalies hotter than
demo configs so these have power). Vertical-agnostic suites live at `tests/` top
level; per-vertical suites in `tests/<vertical>/`. Use the 90-day smoke configs in
CI/quick loops. `tests/golden/b2c_hashes.json` pins the B2C SaaS bytes — regenerate
only on an intentional behavior change (`scripts/golden_hashes.py`).

## Style

Match breakdown/tremor house style: ruff, line length 100, pydantic 2, typer CLI,
`src/` layout, uv-managed. Python: latest stable.
