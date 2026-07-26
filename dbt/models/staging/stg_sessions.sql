with source as (

    select * from {{ source('web', 'sessions') }}

)

select
    session_id,
    anonymous_id,
    user_id,
    channel,
    utm_campaign,
    country,
    device,
    started_at,
    cast(started_at as date) as session_date,
    duration_seconds,
    page_views,
    landing_page,
    _loaded_at
from source
qualify row_number() over (partition by session_id order by _loaded_at desc) = 1
