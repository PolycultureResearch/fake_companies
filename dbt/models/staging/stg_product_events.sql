with source as (

    select * from {{ source('product', 'events') }}

)

select
    event_id,
    user_id,
    event_name,
    plan_at_event,
    country,
    device,
    occurred_at,
    cast(occurred_at as date) as event_date,
    _loaded_at
from source
qualify row_number() over (partition by event_id order by _loaded_at desc) = 1
