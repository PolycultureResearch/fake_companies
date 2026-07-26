-- One row per trial-starting subscription. Grain: subscription_id.
-- `converted` flags trials that later produced a trial_convert event; the
-- conversion ratio metric is conversions / trials_started.
with trial_starts as (

    select
        subscription_id,
        user_id,
        min(occurred_at) as trial_start_at,
        min(event_date)  as trial_start_date
    from {{ ref('stg_subscription_events') }}
    where event_type = 'trial_start'
    group by 1, 2

),

conversions as (

    select
        subscription_id,
        min(occurred_at)                                     as converted_at,
        arg_min(to_plan_id, occurred_at)                     as converted_plan_id
    from {{ ref('stg_subscription_events') }}
    where event_type = 'trial_convert'
    group by 1

)

select
    t.subscription_id,
    t.user_id,
    t.trial_start_at,
    t.trial_start_date,
    c.converted_at,
    c.converted_plan_id,
    coalesce(p.plan_name, 'none')       as plan,
    (c.subscription_id is not null)     as converted,
    u.signup_channel,
    u.country,
    u.device_at_signup                  as device
from trial_starts as t
left join conversions as c
    on t.subscription_id = c.subscription_id
left join {{ ref('dim_plans') }} as p
    on c.converted_plan_id = p.plan_id
left join {{ ref('dim_users') }} as u
    on t.user_id = u.user_id
