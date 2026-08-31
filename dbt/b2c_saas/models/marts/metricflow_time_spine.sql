{{ config(materialized='table') }}

-- Daily time spine for MetricFlow (metric_time__day) and for the
-- fct_subscription_days daily grid. DuckDB generate_series over dates.
with days as (

    select unnest(
        generate_series(
            date '2023-01-01',
            date '2027-12-31',
            interval '1 day'
        )
    ) as date_day

)

select
    cast(date_day as date) as date_day
from days
