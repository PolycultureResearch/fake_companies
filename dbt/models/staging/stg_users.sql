with source as (

    select * from {{ source('app_db', 'users') }}

)

select
    user_id,
    email,
    full_name,
    country,
    signup_channel,
    device_at_signup,
    created_at,
    cast(created_at as date) as signup_date,
    _loaded_at
from source
qualify row_number() over (partition by user_id order by _loaded_at desc) = 1
