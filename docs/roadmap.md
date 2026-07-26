# Roadmap

Future work, in recommended sequence. Each item lists **why**, the **concrete
change** (with file references), **effort**, and **dependencies**. Guiding
principles carried from `AGENTS.md`: config is the API, keep the generic core
business-agnostic, and never break determinism (same seed + config ⇒ byte-identical
output).

Dependency order at a glance:

```
1. Hidden constants -> config      (independent, do anytime)
2. Vertical split  ─┐
3. Domain registries ┘ (3 is a sub-goal of 2 — do together)
4. MCP server        (after 2, so it can target any vertical)
```

---

## 1. Promote the remaining hidden constants to config

**Why.** `AGENTS.md` states "no hidden constants for tunable behavior," but several
business knobs are still module-level constants. They can't be varied per scenario,
which limits realism and blocks agents from asking for, say, a lower payment
success rate or a different diurnal shape. The plan-ladder fix (done) removed the
worst offender; these are what remain.

**Concrete change.** Add optional pydantic fields (defaulting to today's values, so
every existing config keeps working) and read them instead of the constants:

| Constant | Location | Proposed config home | Default |
|---|---|---|---|
| `_CTR`, default `0.02`, `_CAMPAIGN_WEIGHTS` | `entities/marketing.py:19-20,34` | `traffic.channels.<ch>.ctr`, `traffic.campaigns_per_channel` | current |
| `_CURRENCY_BY_COUNTRY` | `entities/billing.py:32` | `billing.currency_by_country` | current map |
| `_P_FIRST_SUCCESS`, `_P_RETRY_SUCCESS`, `_MAX_RETRIES` | `entities/billing.py:46-48` | `billing.payment_success`, `billing.dunning` | 0.975 / 0.80 / 2 |
| `_METHODS`/`_METHOD_WEIGHTS`, `_FAILURE_CODES` | `entities/billing.py:42-44` | `billing.payment_methods`, `billing.failure_codes` | current |
| `_FIRST_DELAY_*`, `_RETRY_DELAY_*` | `entities/billing.py:50-53` | `billing.settlement_lag` | current |
| `SESSION_HOUR_WEIGHTS`, `USAGE_HOUR_WEIGHTS` | `entities/_util.py:14,45` | `traffic.diurnal_shape`, `engagement.diurnal_shape` | current arrays |
| `_LANDING_PAGES`, duration `sigma=0.9` | `entities/traffic.py:29,70` | `traffic.landing_pages`, `traffic.duration_sigma` | current |
| prob-driver `sigma_scale=0.5`, clip bounds | `latent/build.py:103,136` | `noise.prob_sigma_scale` | 0.5 |
| `_DAYS_PER_MONTH=30` | `entities/lifecycle.py:26` | `lifecycle.month_days` | 30 |

**Not** in scope (structural, not "tunable behavior"): lifecycle state codes,
`_NAT`, `billing._PERIOD_DAYS`. Leave those hardcoded.

**Effort.** Small–medium, mechanical, low risk (defaults preserve current output;
determinism tests catch regressions). Do it incrementally — one module per PR.

---

## 2. Vertical split — simulate other kinds of business

**Why.** The generator is a *B2C-SaaS* generator wearing a config. E-commerce
(carts/orders/SKUs, no subscriptions) or B2B SaaS (accounts/seats, sales-led,
contracts) need a different **entity model and table set**, not new config values.
Today a second vertical means forking. This item makes verticals first-class.

**What's already agnostic** (becomes `fake_companies.core`, unchanged in behavior):
`core/` (RngHub, Calendar, curves), `latent/panel.py` + the
`baseline × growth × seasonality × noise × anomaly` machinery, the anomaly
resolution + rate-event application mechanism, `corruption/` (loading + all 5 DQ
corruptions already operate on *any* frame given a schema + event-time — the
cleanest piece), `output/` writer/export/manifest, `groundtruth.py`, `scoring.py`.

**What's B2C-coupled** (moves into `fake_companies.verticals.b2c_saas`):
`entities/*`, `output/schemas.py`, `latent/build.py:known_drivers`/`build_drivers`,
the config sections `funnel`/`plans`/`lifecycle`/`engagement`, the dbt project, and
the domain registries (item 3).

**Concrete change.** Introduce a `Vertical` protocol the core drives:

```python
class Vertical(Protocol):
    name: str
    def config_model(self) -> type[BaseModel]: ...        # vertical-specific YAML sections
    def tables(self) -> list[TableSpec]: ...              # replaces output/schemas.py
    def known_drivers(self, cfg) -> set[str]: ...         # anomaly-target validation
    def build_drivers(self, cfg, cal, rng) -> DriverPanel: ...
    def build_entities(self, cfg, cal, rng, panel, frames) -> None: ...
    def dq_targets(self) -> dict[str, DQTableMeta]: ...   # categorical/nullable/numeric per table
    def affected_metrics(self, driver: str) -> list[str]: ...
```

`generate.py` becomes: resolve the vertical (from `company.vertical` in the config)
→ core builds the panel via `vertical.build_drivers` → applies rate anomalies
(generic) → `vertical.build_entities` → generic corruption over `vertical.tables()`
+ `vertical.dq_targets()` → write. The scenario config grows a `company.vertical:
b2c_saas` field; the vertical contributes its own config sub-model.

**Also vertical-specific:** the dbt project + semantic layer. Each vertical ships
its own `dbt/<vertical>/` (or a generated one). Keep the MetricFlow *contract*
(daily `metric_time__day` series) identical across verticals so Breakdown/Tremor
don't care which vertical produced the data.

**Do it when adding the second vertical** (use e-commerce as the forcing function),
not speculatively — one implementation can't reveal the right seams. The B2C code
becomes the reference implementation of the protocol.

**Effort.** Large. Mostly a move + interface extraction; behavior for B2C must stay
byte-identical (the determinism/honesty suites are the safety net). Land the core
extraction first (no behavior change), then add e-commerce against the protocol.

---

## 3. Collapse the four domain registries into the `Vertical`

**Why.** "What exists in this business" is encoded in **four** places that must be
hand-kept in sync — a standing maintenance trap and the thing that makes a new
vertical error-prone:

| Registry | File | Encodes |
|---|---|---|
| Table schemas | `output/schemas.py` | raw tables, columns, event-time/PK |
| Driver set | `latent/build.py:known_drivers` + `build_drivers` | valid anomaly targets |
| DQ target metadata | `anomalies.py:DQ_TABLE_META` | categorical/nullable/numeric cols per table |
| Driver→metric map | `anomalies.py:_DRIVER_METRICS` | which metrics an anomaly is expected to move |

**Concrete change.** These are exactly the `Vertical` methods in item 2:
`tables()`, `known_drivers()`/`build_drivers()`, `dq_targets()`,
`affected_metrics()`. Collapsing them is not separate work — it *is* the payoff of
the vertical split: one object per vertical is the single source of truth, and the
core validates cross-references (e.g. every dq target in `dq_targets()` is a real
table, every anomaly target is a known driver) generically. Add a `verticals`
conformance test that asserts these invariants for each registered vertical.

**Effort.** Folded into item 2. The one net-new piece is the conformance test.

---

## 4. MCP server — let agents drive it conversationally

**Why.** AI agents are a principal user. The `simulate-company` skill + `docs/using.md`
cover the "author a scenario and run the CLI" path; an MCP server is the endgame
for a claude.ai-connected agent that wants to generate, inspect, and score without
shelling out. Because there's already a typed config + clean CLI + `scoring.py` +
`scripts/verify_consumers.py`, the server is a **thin** wrapper.

**Concrete change.** A stdio MCP server (`fake_companies.mcp`) exposing:

| Tool | Purpose |
|---|---|
| `list_scenarios()` / `describe_scenario(name)` | discover configs + their anomalies |
| `generate_company(vertical, base, overrides, anomalies[], seed, out)` | run generation → `{db_path, ground_truth_summary, manifest}` |
| `list_ground_truth(db)` | the injected anomalies (for known-truth flows) |
| `query_metric(db, metric, grain, dims)` | daily series (wraps `mf`/marts) — Breakdown's path |
| `profile_table(db, table)` | columns + volume/null/PSI stats — Tremor dataflow's path |
| `score(truth, events, tolerance)` | wraps `scoring.score_events` |

Expose `configs/` and `docs/using.md` as MCP **resources**. Ship it as an optional
extra (`fake-companies[mcp]`) so the core install stays lean.

**Do it after item 2** so the tools take a `vertical` argument from day one and
don't bake in B2C assumptions. Until then, the skill is the supported agent entry
point.

**Effort.** Medium, and low-risk since it delegates to existing, tested code.

---

## Not on this roadmap (deliberately)

- Vectorizing `lifecycle._derive_subscriptions` (per-group loop) — it's correct and
  the full run is ~20s, well under the 60s target. Revisit only if a bigger default
  scenario makes it a bottleneck.
- Real PII / faker locale breadth, warehouse targets beyond DuckDB, streaming
  output — out of scope until a consumer needs them.
