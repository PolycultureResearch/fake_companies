-- One row per trial-starting subscription. Grain: subscription_id.
-- `converted` flags trials that later produced a trial_convert event; the
-- conversion ratio metric is conversions / trials_started.
-- `activated` / `days_active` summarize product activity inside the trial
-- window ([trial_start, trial_start + trial_days)): did the user perform
-- the aha-moment event, and on how many distinct days were they active.
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

trial_activity as (

    select
        t.subscription_id,
        count(distinct e.event_date)  as days_active,
        bool_or(e.event_name = '{{ var("trial_activation_event", "upload_work") }}')
                                      as activated
    from trial_starts as t
    join {{ ref('stg_product_events') }} as e
        on e.user_id = t.user_id
        and e.occurred_at >= t.trial_start_at
        and e.occurred_at < t.trial_start_at + interval {{ var('trial_days', 7) }} day
    group by 1

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
    coalesce(a.activated, false)        as activated,
    coalesce(a.days_active, 0)          as days_active,
    u.signup_channel,
    u.country,
    u.device_at_signup                  as device
from trial_starts as t
left join conversions as c
    on t.subscription_id = c.subscription_id
left join trial_activity as a
    on t.subscription_id = a.subscription_id
left join {{ ref('dim_plans') }} as p
    on c.converted_plan_id = p.plan_id
left join {{ ref('dim_users') }} as u
    on t.user_id = u.user_id
