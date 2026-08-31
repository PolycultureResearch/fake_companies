# fake_companies_cpg (cpg_wholesale dbt project)

Models the CPG wholesale raw tables (Bristlecone Botanicals and friends):
staging views -> marts -> MetricFlow semantic layer. Same consumer contract
as every vertical: daily series via
`mf query --metrics <m> --group-by metric_time__day --csv`
(POS metrics are weekly-grain data keyed to their week_start day).

    FAKE_DB=../../out/bristlecone.duckdb dbt build
    mf validate-configs
