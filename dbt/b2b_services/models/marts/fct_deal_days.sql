-- Daily open-pipeline grid: one row per (deal, day the deal was open).
-- Powers open_deals / open_pipeline_value as true daily snapshots. A deal is
-- open from its creation day until the day before it closes (or through the
-- observed timeline end for still-open deals).
with bounds as (

    select max(created_date) as last_day from {{ ref('stg_deals') }}

),

deals as (

    select
        deal_id,
        account_id,
        source,
        industry,
        size_tier,
        amount,
        created_date,
        coalesce(closed_date, (select last_day from bounds) + 1) as end_date
    from {{ ref('stg_deals') }}

)

select
    d.deal_id,
    d.account_id,
    d.source,
    d.industry,
    d.size_tier,
    d.amount,
    t.date_day
from deals as d
inner join {{ ref('metricflow_time_spine') }} as t
    on t.date_day >= d.created_date
    and t.date_day < d.end_date
    and t.date_day <= (select last_day from bounds)
