-- M5 reconciliation gate: fct_subscription_days must match an INDEPENDENT
-- raw-SQL recomputation of active subscriptions and MRR for a sample of days.
--
-- The mart builds daily state via segment explosion (lead over the event
-- stream). Here we recompute the same quantities a different way:
-- "latest-event-wins" — for each sampled day, take each subscription's most
-- recent event on/before that day; it is active iff that event created a plan.
-- Any day where active_subs or MRR disagree is returned (=> test fails).

with sample_days as (

    -- ~3 days per month across the whole range, config-independent.
    select distinct date_day
    from {{ ref('fct_subscription_days') }}
    where extract(day from date_day) in (5, 15, 25)

),

plan_price as (

    select
        plan_id,
        case when billing_period = 'annual' then price / 12.0 else price end
            as monthly_price
    from {{ source('app_db', 'plans') }}

),

raw_latest as (

    select
        d.date_day,
        se.subscription_id,
        se.event_type,
        se.to_plan_id,
        row_number() over (
            partition by d.date_day, se.subscription_id
            order by se.occurred_at desc, se.event_id desc
        ) as rn
    from sample_days as d
    inner join {{ source('app_db', 'subscription_events') }} as se
        on cast(se.occurred_at as date) <= d.date_day

),

raw_active as (

    select
        rl.date_day,
        rl.subscription_id,
        rl.to_plan_id as plan_id
    from raw_latest as rl
    where rl.rn = 1
      and rl.event_type in ('trial_convert', 'upgrade', 'downgrade', 'resurrect')

),

raw_agg as (

    select
        ra.date_day,
        count(*)                    as active_subs,
        sum(pp.monthly_price)       as mrr
    from raw_active as ra
    left join plan_price as pp on ra.plan_id = pp.plan_id
    group by 1

),

mart_agg as (

    select
        date_day,
        count(*)            as active_subs,
        sum(mrr_amount)     as mrr
    from {{ ref('fct_subscription_days') }}
    where extract(day from date_day) in (5, 15, 25)
    group by 1

)

select
    coalesce(m.date_day, r.date_day)        as date_day,
    m.active_subs                           as mart_active_subs,
    r.active_subs                           as raw_active_subs,
    m.mrr                                   as mart_mrr,
    r.mrr                                   as raw_mrr
from mart_agg as m
full outer join raw_agg as r on m.date_day = r.date_day
where coalesce(m.active_subs, 0) != coalesce(r.active_subs, 0)
   or abs(coalesce(m.mrr, 0) - coalesce(r.mrr, 0)) > 0.01
