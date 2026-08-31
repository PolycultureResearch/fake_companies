-- Daily active-subscription snapshot. Grain: (subscription_id, date_day).
--
-- The active plan for each subscription is a step function driven by the
-- event stream (subscriptions.plan_id is only the *final* plan). We rebuild it:
-- each event sets the resulting active plan, which holds until the next event.
--   trial_convert / upgrade / downgrade / resurrect -> active on to_plan_id
--   cancel / trial_expire / trial_start             -> inactive (no plan)
-- Segments with an active plan are exploded across the time spine (bounded to
-- the last observed data date), one row per active subscription per day, and
-- carry that day's monthly-equivalent MRR contribution.
with events as (

    select
        subscription_id,
        event_type,
        occurred_at,
        event_date,
        case
            when event_type in ('trial_convert', 'upgrade', 'downgrade', 'resurrect')
                then to_plan_id
            else null
        end as active_plan_id
    from {{ ref('stg_subscription_events') }}

),

segments as (

    select
        subscription_id,
        active_plan_id,
        event_date as valid_from,
        lead(event_date) over (
            partition by subscription_id
            order by occurred_at, event_date
        ) as valid_to
    from events

),

data_bounds as (

    select max(max_date) as max_date
    from (
        select max(session_date) as max_date from {{ ref('stg_sessions') }}
        union all
        select max(event_date)   as max_date from {{ ref('stg_subscription_events') }}
        union all
        select max(payment_date) as max_date from {{ ref('stg_payments') }}
    )

),

active_segments as (

    select
        subscription_id,
        active_plan_id,
        valid_from,
        valid_to
    from segments
    where active_plan_id is not null

),

exploded as (

    select
        seg.subscription_id,
        seg.active_plan_id as plan_id,
        ts.date_day
    from active_segments as seg
    cross join data_bounds as b
    inner join {{ ref('metricflow_time_spine') }} as ts
        on ts.date_day >= seg.valid_from
        and (seg.valid_to is null or ts.date_day < seg.valid_to)
        and ts.date_day <= b.max_date

)

select
    cast(e.subscription_id as varchar) || '-' || cast(e.date_day as varchar)
        as subscription_day_id,
    e.date_day,
    e.subscription_id,
    s.user_id,
    e.plan_id,
    p.plan_name as plan,
    p.billing_period,
    p.monthly_price as mrr_amount
from exploded as e
left join {{ ref('dim_plans') }} as p on e.plan_id = p.plan_id
left join {{ ref('stg_subscriptions') }} as s on e.subscription_id = s.subscription_id
