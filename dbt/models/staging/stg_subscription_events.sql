with source as (

    select * from {{ source('app_db', 'subscription_events') }}

)

select
    event_id,
    subscription_id,
    user_id,
    event_type,
    from_plan_id,
    to_plan_id,
    occurred_at,
    cast(occurred_at as date) as event_date,
    _loaded_at
from source
qualify row_number() over (partition by event_id order by _loaded_at desc) = 1
