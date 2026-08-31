-- One row per closed-won deal, bucketed by close date. Grain: deal_id.
select
    d.deal_id,
    d.account_id,
    d.source,
    d.industry,
    d.size_tier,
    d.region,
    d.amount,
    d.closed_date,
    d.cycle_days
from {{ ref('fct_deals') }} as d
where d.stage = 'closed_won' and d.closed_date is not null
