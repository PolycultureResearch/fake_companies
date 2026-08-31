# fake_companies_b2b (b2b_services dbt project)

Models the B2B services raw tables (Meridian Partners and friends):
staging views -> marts -> MetricFlow semantic layer. Same consumer contract
as every vertical: daily series via
`mf query --metrics <m> --group-by metric_time__day --csv`.

    FAKE_DB=../../out/meridian.duckdb dbt build
    mf validate-configs
