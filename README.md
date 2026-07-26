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

**Using it to build a dataset?** See [`docs/using.md`](docs/using.md) for a
recipe cookbook (inject an MRR drop, a pipeline outage, a segmented regression,
a blind test). AI agents: invoke the **`simulate-company`** skill.
Future work (other verticals, MCP server, config'able constants) is in
[`docs/roadmap.md`](docs/roadmap.md).

## Quickstart

```bash
uv sync
uv run fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb
uv run fake-companies truth  --db out/acme.duckdb        # list injected anomalies
uv run fake-companies export --db out/acme.duckdb --format parquet
```

Model the raw tables with the companion dbt project and query metrics via
MetricFlow (this is Breakdown's exact fetch path):

```bash
uv sync --extra dbt
export DBT_PROFILES_DIR=$PWD/dbt FAKE_DB=$PWD/out/acme.duckdb
uv run dbt build --project-dir dbt
cd dbt && uv run mf validate-configs
uv run mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv
```

Verify both consumer contracts (Breakdown tree metrics + Tremor dataflow tables)
end-to-end:

```bash
FAKE_DB=out/acme.duckdb uv run python scripts/verify_consumers.py
```

### Blind-testing a detector

Injected anomalies (scripted + `surprise`-sampled) are recorded in
`ground_truth.json`. Don't open it, run your detector, then score:

```bash
uv run fake-companies score --truth ground_truth.json --events my_detector_events.json --tolerance 3
# -> precision / recall / f1 with +-k day bucket tolerance
```

## What you get

The default `acme_b2c_saas.yaml` scenario (2 years, seed-deterministic) produces
roughly:

| table | rows | notes |
|-------|------|-------|
| `web.sessions` | ~1.3M | event_time + nullable user_id; PSI/volume targets |
| `product.events` | ~7.5M | NHPP usage, diurnal + weekend shape (Tremor dataflow star) |
| `app_db.users` | ~50k | faker attributes |
| `app_db.subscriptions` | ~31k spells | ~8.8k ever-paid; MRR movements telescope exactly |
| `billing.payments` | ~— | dunning, ~5% failures (PSI on currency, quantiles on amount) |
| `meta.ground_truth` | 12+ | every injected rate/dq anomaly, with expected affected signals |

21 MetricFlow metrics (`marketing_spend`, `mrr`, `visit_signup_rate`, `dau`,
`wau`, `payment_failure_rate`, …) queryable at daily grain. Full run ≈ 20s.

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
