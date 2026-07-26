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
uv run fake-companies generate --config configs/acme_b2c_saas.yaml --out out/acme.duckdb
uv run fake-companies truth --db out/acme.duckdb          # what anomalies were injected
```

- `configs/acme_b2c_saas.yaml` — canonical 2-year B2C SaaS company (~1.3M sessions,
  ~50k users, ~7.5M product events). Full run ≈ 20s.
- `configs/smoke_90d.yaml` — 90 days, small, seconds to run. Use for fast loops/CI.

To iterate on your own scenario, **copy** a config and edit the copy:
`cp configs/acme_b2c_saas.yaml configs/my_scenario.yaml`.

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
  export DBT_PROFILES_DIR=$PWD/dbt FAKE_DB=$PWD/out/acme.duckdb
  uv run dbt build --project-dir dbt
  uv run mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv   # in dbt/
  ```
  `examples/breakdown_acme_tree.yml` is a ready metric tree. `scripts/verify_consumers.py`
  checks every tree metric + Tremor table against a built db.
- **Tremor dataflow mode** profiles the raw tables directly (needs `event_time` +
  `_loaded_at`). See `examples/tremor_acme.yaml`.

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
`visit_signup_rate`, `trials_started`, `trial_conversion_rate`,
`new_subscriptions`, `churned_subscriptions`, `active_subscriptions`, `mrr`,
`new_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr`, `arpu`,
`customer_churn_rate`, `dau`, `wau`, `product_events`, `revenue`,
`payment_failure_rate`. Dimensions where grain allows: `plan`, `signup_channel`,
`country`, `device`.

> Current scope is B2C SaaS (freemium + trial → subscription). Plan tiers,
> channels, and countries are config-driven, but the entity model (sessions →
> signups → subscriptions → billing → usage) is fixed. Other verticals
> (e-commerce, B2B) are on the roadmap.
