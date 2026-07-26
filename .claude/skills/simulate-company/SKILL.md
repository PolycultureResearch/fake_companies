---
name: simulate-company
description: "Spin up a realistic synthetic company dataset with fake_companies — to test an anomaly detector (Tremor), demo root-cause analysis (Breakdown), or produce a fixture with specific trends/anomalies. Use when someone wants generated company data, a dataset containing a particular anomaly (an MRR drop, a pipeline outage, a segmented regression), a blind-test dataset to score a detector against, or daily KPI series to point an analytics tool at."
metadata:
  author: PolycultureResearch
---

# Simulate a company

Drive the `fake_companies` generator to produce a deterministic synthetic dataset
for a purpose (testing/demoing an analytics tool, or a fixture). The scenario YAML
is the API; `docs/using.md` is the recipe cookbook — read it for anomaly blocks,
driver names, and metric names.

## 1. Clarify the goal (ask only what you can't infer)

- **Purpose?** Tremor KPI monitoring, Tremor dataflow (raw-table) monitoring,
  Breakdown RCA, a plain fixture, or a blind detector test.
- **What should be in the data?** A specific anomaly (revenue drop, pipeline
  outage, segmented regression), or just a healthy baseline?
- **Scale / speed?** Fast iteration → 90-day smoke; realistic demo → 2-year acme.
- **Known or blind?** Blind test → use `anomalies.surprise` and don't read
  `ground_truth.json` until scoring.

## 2. Set up

```bash
cd <fake_companies repo>
uv sync                       # add --extra dbt if KPI metrics are needed (step 4)
```

## 3. Pick or author a scenario

- Baseline only, fast → `configs/smoke_90d.yaml`. Realistic → `configs/acme_b2c_saas.yaml`.
- Custom → **copy** a config and edit the copy; add anomaly blocks from the
  recipes in `docs/using.md`. Common ones:
  - revenue drop → `rate` `level_shift` on `spend.<channel>` or `churn.<plan>`
  - pipeline outage → `dq` `volume_dropout` on `product.events`
  - segmented regression → add `segment: { country: US, device: mobile }`
  - blind test → an `anomalies.surprise` block
- Inspect available drivers with `--dump-drivers out/drivers.csv` if unsure of a target.

Generate:

```bash
uv run fake-companies generate --config configs/<your>.yaml --out out/company.duckdb
uv run fake-companies truth --db out/company.duckdb    # confirm injected anomalies
```

Raw tables land in the DuckDB (schemas `ad_platform`, `web`, `app_db`, `billing`,
`product`); `ground_truth.json` + `run_manifest.json` are written alongside.

## 4. Model KPIs (only if the consumer needs daily metrics)

Tremor KPI mode and Breakdown need the dbt/MetricFlow layer:

```bash
uv sync --extra dbt
export DBT_PROFILES_DIR=$PWD/dbt FAKE_DB=$PWD/out/company.duckdb
uv run dbt build --project-dir dbt
# each metric is then queryable at daily grain (Breakdown's fetch path):
cd dbt && uv run mf query --metrics mrr --group-by metric_time__day --csv /tmp/mrr.csv
```

Tremor **dataflow** mode needs no dbt — it profiles the raw tables directly
(`event_time` + `_loaded_at` columns are already present).

## 5. Point the consumer at it

- **Breakdown:** use `examples/breakdown_acme_tree.yml` (a metric tree over the
  semantic layer); its `LocalDataFetcher` runs the `mf query` above.
- **Tremor:** use `examples/tremor_acme.yaml` (KPI + dataflow monitors).
- Sanity-check both contracts at once:
  `FAKE_DB=out/company.duckdb uv run python scripts/verify_consumers.py`.

## 6. Blind-test scoring (if applicable)

After the detector emits events (a JSON list of `{detected_at, metric}` or Tremor
`AnomalyEvent`s), score without peeking:

```bash
uv run fake-companies score --truth ground_truth.json --events detector_events.json --tolerance 3
```

## 7. Report back

Tell the user: the database path, a one-line summary of the injected ground truth
(`fake-companies truth`), and the exact command/config to point their tool at it.
Keep it reproducible — mention the `seed` so they can regenerate byte-identically.

## Scope note

Current scope is **B2C SaaS** (freemium + trial → subscription). Plan tiers,
channels, countries, trends, seasonality, and anomalies are all config-driven, but
the entity model (sessions → signups → subscriptions → billing → usage) is fixed.
If asked for e-commerce or B2B SaaS, say those verticals aren't implemented yet
(they're on the roadmap) rather than forcing a B2C shape onto them.
