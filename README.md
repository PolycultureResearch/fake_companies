# fake_companies

Realistic **synthetic raw company data** with ground-truth-labeled anomalies — a
generator for testing and demoing analytics software without exposing real client
data. Built to feed [Breakdown](https://github.com/PolycultureResearch) (Bayesian
metric-tree root-cause analysis) and Tremor (anomaly detection).

It simulates a B2C SaaS company (freemium + trial → subscription) from the bottom
up: a latent daily-rate panel (trends, seasonality, noise) drives entity-level raw
rows (sessions, signups, subscriptions, billing, product events), then an
observation layer models connector loading lag and data-quality faults. Injected
anomalies carry **ground truth** so you can score detectors.

## Quickstart

```bash
uv sync
uv run fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb
uv run fake-companies truth --db out/acme.duckdb        # list injected anomalies
uv run fake-companies export --db out/acme.duckdb --format parquet
```

Then model with the companion dbt project and query metrics via MetricFlow:

```bash
cd dbt && FAKE_DB=../out/acme.duckdb dbt build
mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv
```

## Architecture

Three separated layers (see `AGENTS.md` and `docs/plan.md`):

1. **`latent/`** — config-driven daily rate panel per (driver, segment). Rate
   anomalies applied here cascade causally downstream (spend cut → fewer sessions
   → fewer signups → less MRR).
2. **`entities/`** — raw rows drawn stochastically from the latent rates
   (Poisson sessions, binomial signups, hazard-based subscription lifecycles,
   NHPP usage). Aggregate realism *emerges*; nothing aggregate is written directly.
3. **`corruption/`** — observation layer: `_loaded_at` connector models and
   data-quality corruptions. Business truth unchanged; only observed rows mutate.

Everything is deterministic: same `seed` + config ⇒ byte-identical output.

## Development

```bash
uv run pytest -m "not slow"       # fast loop
uv run pytest                     # full statistical suite
uv run ruff check . && uv run ruff format .
```

License: MIT.
