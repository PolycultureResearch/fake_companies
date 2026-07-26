# fake_companies — Realistic B2C SaaS Raw-Data Generator

## Session handoff (read first)

Status: **planning complete, zero implementation.** `/Users/devon/Documents/code/fake_companies/` is an empty directory — no git repo, no files. This document is the complete spec; no other context is required beyond the referenced files in the breakdown/tremor repos.

First actions in the implementation session:
1. `git init` the project directory.
2. Write `AGENTS.md` from the draft in the "AGENTS.md draft" section at the bottom of this plan (also symlink or duplicate as `CLAUDE.md`).
3. Copy this plan into the repo as `docs/plan.md` so the spec is versioned with the code.
4. Proceed milestone by milestone (M0 → M8), verifying each gate before moving on.

## Context

Devon develops two analytics packages — **Breakdown** (Bayesian metric-tree RCA) and **Tremor** (anomaly detection) — and lacks real company datasets to test/demo them without exposing client data. This project generates realistic **raw** company data (modeled downstream with dbt), with stochastic generation, realistic trends/seasonality, and injected anomalies/change points **with ground truth** for scoring detectors. SOMA-B2B-SaaS's fake-data macro was reviewed and rejected as a base: it's uniform-random with no temporal structure.

**Consumer contracts (verified in code):**
- Breakdown (`breakdown/data_fetch.py`): fetches daily metric series via `mf query --metrics X --group-by metric_time__day --csv` run in a dbt project dir. Needs a working MetricFlow semantic layer + a tree YAML referencing metric names.
- Tremor KPI mode: same MetricFlow CSVs; needs ≥90 days history, weekly seasonality.
- Tremor dataflow mode (`examples/demo_dataflow.py`, `config/tremor.yaml`): profiles raw tables needing `event_time` + `_loaded_at` columns, categorical columns for PSI shifts, numeric columns for quantile drift, nullable FKs, unique PKs, hourly volume patterns.

**Decisions (user-confirmed):** v1 = raw data + dbt project + MetricFlow semantic layer; core funnel + engagement breadth; freemium + trial → subscription (Free/Basic/Pro, monthly/annual); borrow SOMA metric naming where sensible (adapted to B2C); latest stable Python (Devon will update Tremor/Breakdown if needed).

**Tremor volatility note:** Tremor is in very early development and its internals may change substantially. The stable contract to build against is: (a) raw warehouse tables with event-time + loaded-at columns, and (b) modeled KPIs queryable at daily grain via MetricFlow. Keep Tremor-specific artifacts thin and example-level (`examples/tremor_acme.yaml`); don't couple the generator to Tremor's current detector/config details.

## Architecture: three-layer latent-rate simulation

1. **Latent driver panel (macro):** config-driven daily rates per (driver, segment): `rate = baseline × growth(t) × weekly(dow) × annual(doy) × holiday × AR(1)-lognormal noise × anomaly multiplier`. **Rate anomalies (spike/drop, level_shift, trend_change, seasonality_change) are applied here** so effects propagate causally downstream (spend cut → sessions → signups → MRR) — exactly what Breakdown's RCA should recover.
2. **Entity simulation (micro):** raw rows drawn from latent rates — sessions ~ Poisson, signups ~ Binomial, per-user lifecycle hazard state machine (trial/convert/churn/upgrade/downgrade/resurrect), NHPP usage events with per-user lognormal frailty, billing from subscription spells (incl. dunning/failures). Aggregate realism *emerges*; nothing aggregate is written directly.
3. **Post-hoc corruption + loading model:** `_loaded_at` from per-source connector models (batch cadence + lognormal lag). Data-quality anomalies (volume_dropout, null_spike, distribution_shift, loading_delay, duplicate_rows) mutate finished raw frames — observation corruptions, matching Tremor dataflow semantics.

**Ground truth:** every injected event emits a `GroundTruthRecord` (pydantic: id, kind rate/dq, type, driver/table, segment, start/end, magnitude, affected_metrics/signals) → `meta.ground_truth` table + `ground_truth.json`.

**Stack:** Latest stable Python — `requires-python = ">=3.13"`, develop on the newest CPython that the dependency chain supports (3.14 if dbt-core/MetricFlow/dbt-duckdb install cleanly there at setup time, else 3.13; verify at M0 and pin `.python-version` accordingly). Devon will update Tremor/Breakdown to match if needed. Deps: uv, numpy, pandas, pyarrow, duckdb, pydantic 2, pyyaml, typer, faker, holidays. Vectorized numpy → arrow → DuckDB; target <~60s for 2-year default scenario. Ruff, line-length 100 (house style of both repos).

## Package layout

```
fake_companies/
├── pyproject.toml
├── configs/acme_b2c_saas.yaml      # canonical scenario
├── src/fake_companies/
│   ├── cli.py                      # typer: generate / export / truth / score
│   ├── config/schema.py            # pydantic ScenarioConfig mirroring YAML
│   ├── config/loader.py
│   ├── core/rng.py                 # RngHub: SeedSequence.spawn per named stream
│   ├── core/calendar.py            # date grid, dow/doy, holiday mask
│   ├── core/curves.py              # linear/exponential/logistic/piecewise growth
│   ├── latent/panel.py             # DriverPanel: dates × {(driver, segment): ndarray}
│   ├── latent/build.py             # build_drivers(cfg, cal, rng) -> DriverPanel
│   ├── latent/events.py            # apply_rate_events -> (panel, [GroundTruthRecord])
│   ├── entities/marketing.py       # ad_spend
│   ├── entities/traffic.py         # sessions (Poisson, intraday shape)
│   ├── entities/funnel.py          # signups -> users (faker attrs)
│   ├── entities/lifecycle.py       # subscription hazard state machine (vectorized cohorts)
│   ├── entities/billing.py         # invoices + payments (dunning, ~3-5% failures)
│   ├── entities/usage.py           # product events: NHPP per active user-day, frailty
│   ├── corruption/loading.py       # _loaded_at connector models
│   ├── corruption/dq.py            # apply_dq_events -> (tables, [GroundTruthRecord])
│   ├── output/duckdb_writer.py     # schemas: ad_platform/web/app_db/billing/product/meta
│   ├── output/export.py            # parquet/csv export
│   ├── output/manifest.py          # ground_truth.json + run_manifest.json (seed, cfg hash)
│   ├── groundtruth.py
│   └── generate.py                 # orchestrator
├── dbt/                            # companion dbt-duckdb project
│   ├── profiles.yml                # path: env_var('FAKE_DB', '../out/acme.duckdb')
│   ├── models/staging/  models/marts/  models/semantic/  metricflow_time_spine.sql
│   └── tests/
├── examples/breakdown_acme_tree.yml  examples/tremor_acme.yaml
└── tests/                          # determinism, curves, latent, lifecycle/funnel stats,
                                    # anomaly injection, dq corruption, end-to-end
```

## Raw table schemas (all tables carry `_loaded_at`)

- `ad_platform.ad_spend` — spend_id PK, date, channel, campaign_id, impressions, clicks, spend, currency (daily batch, next-morning load)
- `web.sessions` — session_id PK, anonymous_id, user_id NULL, channel (paid_search|paid_social|display|organic|direct|referral|email), utm_campaign NULL, country, device, started_at (event_time), duration_seconds, page_views, landing_page
- `app_db.users` — user_id PK, email, full_name, country, signup_channel, device_at_signup, created_at
- `app_db.plans` — plan_id PK, name (free|basic|pro), billing_period, price, currency
- `app_db.subscriptions` — one row per spell: subscription_id PK, user_id, plan_id, status (trialing|active|past_due|canceled), trial_start_at, trial_end_at, started_at, canceled_at NULL
- `app_db.subscription_events` — MRR-movement source: event_id PK, subscription_id, user_id, event_type (trial_start|trial_convert|new|upgrade|downgrade|cancel|resurrect|trial_expire), from_plan_id, to_plan_id, occurred_at
- `billing.invoices` — invoice_id PK, subscription_id, user_id, amount, currency, period_start/end, status, issued_at
- `billing.payments` — Tremor dataflow star: payment_id PK, invoice_id, user_id, amount, currency (PSI target), payment_method, status, failure_code NULL, created_at (event_time)
- `product.events` — big table: event_id PK, user_id, event_name (7-ish features), plan_at_event, country, device, occurred_at (day/night intraday shape), 15-min micro-batch loading
- `meta.ground_truth`, `meta.run_manifest`

Default scale: 730 days, sessions ~800→4,000/day, ~60-80k users, ~8-12k ever-paid subs, ~2-8M product events.

## Config YAML shape (pydantic-mirrored)

Sections: `company`, `seed`, `timeline`, `calendar` (holiday country/effect, annual_amp), `weekly_shape`, `noise` (day_sigma, ar1), `traffic` (organic w/ logistic growth; paid channels w/ spend, cpc), `mix` (country/device), `funnel` (signup_rate per channel, trial_start_rate), `plans` (prices, annual_share), `lifecycle` (trial_days, trial_convert, monthly_churn per plan, upgrade/downgrade/resurrect rates), `engagement` (dau_over_active per plan, events_per_active_day + frailty_sigma, feature_mix), `loading` (per-source cadence/lag), `anomalies`.

### User control surface (trends, seasonality, anomalies, breakpoints)

Everything is controlled from the scenario YAML; the pydantic `ScenarioConfig` mirrors it 1:1 and validates (unknown drivers, out-of-range dates, bad segment keys → load-time errors).

1. **Trends** — every latent driver takes a `growth` block: `kind: flat|linear|exponential|logistic|piecewise`. `piecewise` is a list of `{from: date, kind, rate}` segments, so structural breakpoints in growth itself are scripted directly (e.g. "growth stalls in March"). These are *baseline shape*, not anomalies — they don't appear in ground truth.
2. **Seasonality** — global `weekly_shape` (per-DOW multipliers), `calendar.annual_amp` (sinusoidal annual cycle), `calendar.holiday_country/effect` (holidays library mask); per-driver overrides allowed (e.g. B2C usage rises on weekends while signups dip).
3. **Stochasticity** — `noise.day_sigma` + `noise.ar1` (lognormal AR(1) day effect shared per driver), entity-level randomness (Poisson/Binomial/hazard draws, per-user frailty). All from `seed` via named `RngHub` streams — same seed, same company.
4. **Scripted anomalies/changepoints** — `anomalies.scripted`: a list of event objects, each `{name, kind: rate|dq, type, target, window, magnitude, segment?, params?}`:
   - **rate events** target a *driver* and multiply its latent rate, so effects cascade causally downstream: `spike`/`drop` (single day or short window), `level_shift` (step change from t0, optionally recovering at t1), `trend_change` (compounding daily factor — a slope breakpoint), `seasonality_change` (rescale/replace the DOW shape from t0), `ramp` (gradual drift to a new level). Optional `segment:` filter confines the event to e.g. `{device: mobile, country: US}` — invisible in the topline until you slice, ideal for Breakdown dimensional-RCA demos.
   - **dq events** target a *raw table* at the observation layer (business truth unchanged): `volume_dropout`, `null_spike` (column), `distribution_shift` (categorical new_mix), `loading_delay` (freshness), `duplicate_rows`.
5. **Surprise mode** — `anomalies.surprise`: `{count, kinds/types allowlist, magnitude: {min,max}, min_gap_days, exclude_windows}`; generator samples events from its own seeded stream. Ground truth still recorded, written only to `ground_truth.json` (and `meta.ground_truth`) — don't open it and you can blind-test detectors, then score with `fake-companies score`.
6. **Ground truth** — every scripted + surprise event (never baseline trend/seasonality) emits a `GroundTruthRecord` with window, magnitude, target, segment, and expected affected metrics/signals.

## dbt project

- **staging** (views): 1:1 stg_* per raw table; sources.yml with `loaded_at_field: _loaded_at` freshness
- **marts** (tables): dim_users, dim_plans, fct_sessions, fct_signups, fct_trials (converted flag → conversion ratio metric), fct_mrr_movements (events × plan prices), fct_subscription_days (daily date × subscription snapshot via time spine → active_subs/mrr/arpu), fct_invoices, fct_payments, fct_activity_days (→ DAU/WAU), metricflow_time_spine
- **semantic layer** metrics: marketing_spend, sessions, signups, visit_signup_rate, trials_started, trial_conversion_rate, new/churned/active_subscriptions, mrr + new/expansion/contraction/churned_mrr, arpu (derived), customer_churn_rate, dau, wau (cumulative 7d), product_events, revenue, payment_failure_rate. Dimensions everywhere grain allows: plan, signup_channel, country, device.
- Use the `building-dbt-semantic-layer` skill when writing semantic YAML; gate with `mf validate-configs`.
- `examples/breakdown_acme_tree.yml`: marketing_spend → sessions → signups → trials → new_subscriptions → new_mrr; mrr = arpu × active_subscriptions (formula node).
- `examples/tremor_acme.yaml`: kpi monitors (signups, mrr, visit_signup_rate) + dataflow monitors on product.events and billing.payments (volume, null_rate, PSI on currency, quantiles on amount).

## Milestones (each verified before moving on)

1. **M0 Scaffold** — pyproject, config schema/loader, RngHub, CLI writing empty DuckDB + run_manifest. ✓ CLI runs; same seed → same manifest; pytest green.
2. **M1 Latent layer** — calendar/curves/panel/build + apply_rate_events + ground truth. ✓ unit tests; injected level shift changes windowed mean by factor ±2%; `--dump-drivers` CSV for eyeballing.
3. **M2 Marketing → traffic → funnel** — ad_spend, sessions, users. ✓ DuckDB checks: daily counts within Poisson tolerance; signup rates within binomial CI; weekend dip present.
4. **M3 Lifecycle + billing** — subscriptions, subscription_events, invoices, payments. ✓ conservation: MRR movements telescope to final MRR; invoice chains unbroken; cohort churn matches hazard; trial conversion ≈ config.
5. **M4 Usage + loading + DQ corruption** — product.events, _loaded_at, apply_dq_events. ✓ intraday shape; corrupted windows measurably shifted using the same stats Tremor computes (volume, null rate, PSI) in DuckDB.
6. **M5 dbt models** — ✓ `dbt build` green; fct_subscription_days matches raw-SQL recomputation.
7. **M6 Semantic layer** — ✓ `mf validate-configs`; `mf query --group-by metric_time__day --csv` non-empty for every metric (Breakdown's exact fetch path).
8. **M7 Consumer integration + scoring** — examples for both tools; `fake-companies score --truth ... --events ...` (precision/recall with ±k-bucket tolerance). ✓ Breakdown LocalDataFetcher returns frames for every tree node; Tremor fires on scripted connector_outage.
9. **M8 Hardening** — determinism test (same seed → identical parquet hashes), slow statistical suite, small 90-day smoke config, README quickstart.

## Testing strategy

- Determinism: byte-identical exports under fixed seed.
- Statistical: tolerance assertions vs configured rates (Poisson/binomial CIs); DOW seasonality F-test; cohort survival vs hazard.
- Ground-truth honesty: recompute each injected anomaly's affected aggregate; assert effect ≥ ~3 robust sigmas vs clean window (detectable-in-principle before blaming a detector).
- dbt: PK unique/not_null, relationships, freshness, reconciliation singular tests; `mf validate-configs`.

## Key reference files

- /Users/devon/Documents/code/breakdown/breakdown/data_fetch.py (fetch contract), examples/jaffle_shop_tree.yml, tests/synthetic.py
- /Users/devon/Documents/code/tremor/tests/synth.py (existing injectors to mirror), tremor/models.py, examples/demo_dataflow.py, config/tremor.yaml
- SOMA metric naming reference: https://github.com/Levers-Labs/SOMA-B2B-SaaS (definitions/metrics/*.json)

## AGENTS.md draft (write verbatim into the repo at M0, adjust as code evolves)

```markdown
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
```
