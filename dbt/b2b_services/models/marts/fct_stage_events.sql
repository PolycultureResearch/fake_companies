-- One row per stage transition. Grain: event_id.
select
    e.event_id,
    e.deal_id,
    e.account_id,
    e.stage,
    e.occurred_at,
    e.occurred_date,
    d.source,
    d.industry,
    d.size_tier
from {{ ref('stg_stage_events') }} as e
left join {{ ref('stg_deals') }} as d
    on e.deal_id = d.deal_id
