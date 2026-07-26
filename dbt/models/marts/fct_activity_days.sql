-- One row per active user per day, from product events. Grain: (user_id, event_date).
-- Feeds DAU (distinct users/day) and WAU (cumulative 7-day distinct users).
with events as (

    select
        user_id,
        event_date,
        occurred_at,
        plan_at_event,
        country,
        device
    from {{ ref('stg_product_events') }}

),

daily as (

    select
        user_id,
        event_date,
        count(*)                              as event_count,
        arg_max(plan_at_event, occurred_at)   as plan,
        arg_max(country, occurred_at)         as country,
        arg_max(device, occurred_at)          as device
    from events
    group by 1, 2

)

select
    cast(user_id as varchar) || '-' || cast(event_date as varchar) as user_day_id,
    user_id,
    event_date,
    event_count,
    plan,
    country,
    device
from daily
