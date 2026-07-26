-- One row per MRR-moving subscription event. Grain: event_id.
-- Movement amount uses monthly-equivalent price (annual = price/12), so
-- movements telescope to active MRR. Category buckets:
--   trial_convert -> new, resurrect -> new (reactivation),
--   upgrade -> expansion, downgrade -> contraction, cancel -> churned.
with events as (

    select *
    from {{ ref('stg_subscription_events') }}
    where event_type in ('trial_convert', 'resurrect', 'upgrade', 'downgrade', 'cancel')

),

priced as (

    select
        e.event_id,
        e.subscription_id,
        e.user_id,
        e.event_type,
        e.event_date,
        e.occurred_at,
        e.from_plan_id,
        e.to_plan_id,
        coalesce(pt.monthly_price, 0.0) as to_monthly_price,
        coalesce(pf.monthly_price, 0.0) as from_monthly_price,
        coalesce(pt.plan_name, pf.plan_name) as plan
    from events as e
    left join {{ ref('dim_plans') }} as pt on e.to_plan_id = pt.plan_id
    left join {{ ref('dim_plans') }} as pf on e.from_plan_id = pf.plan_id

),

categorized as (

    select
        *,
        to_monthly_price - from_monthly_price as movement_amount,
        case
            when event_type in ('trial_convert', 'resurrect') then 'new'
            when event_type = 'upgrade'                       then 'expansion'
            when event_type = 'downgrade'                     then 'contraction'
            when event_type = 'cancel'                        then 'churned'
        end as category
    from priced

)

select
    event_id,
    subscription_id,
    user_id,
    event_type,
    event_date,
    occurred_at,
    from_plan_id,
    to_plan_id,
    plan,
    category,
    movement_amount,
    case when category = 'new'         then movement_amount     else 0.0 end as new_mrr_amount,
    case when category = 'expansion'   then movement_amount     else 0.0 end as expansion_mrr_amount,
    case when category = 'contraction' then -movement_amount    else 0.0 end as contraction_mrr_amount,
    case when category = 'churned'     then -movement_amount    else 0.0 end as churned_mrr_amount,
    case when event_type = 'trial_convert' then 1 else 0 end as is_new_subscription,
    case when event_type = 'cancel'        then 1 else 0 end as is_churned_subscription
from categorized
