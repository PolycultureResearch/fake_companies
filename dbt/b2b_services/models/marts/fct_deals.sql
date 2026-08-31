-- One row per deal. Grain: deal_id.
select
    d.deal_id,
    d.account_id,
    d.contact_id,
    d.source,
    d.industry,
    d.size_tier,
    d.region,
    d.stage,
    d.amount,
    d.created_at,
    d.created_date,
    d.closed_at,
    d.closed_date,
    date_diff('day', d.created_at, d.closed_at) as cycle_days
from {{ ref('stg_deals') }} as d
