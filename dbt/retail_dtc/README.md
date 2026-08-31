# fake_companies_retail (retail_dtc dbt project)

Models the retail DTC raw tables (Alpenglow Supply Co. and friends):
staging views -> marts -> MetricFlow semantic layer. Same consumer contract
as every vertical: daily series via
`mf query --metrics <m> --group-by metric_time__day --csv`.

    FAKE_DB=../../out/alpenglow.duckdb dbt build
    mf validate-configs
