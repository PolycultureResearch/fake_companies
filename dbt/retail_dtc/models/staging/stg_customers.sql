with source as (

    select * from {{ source('shop_db', 'customers') }}

)

select
    customer_id,
    email,
    full_name,
    country,
    first_channel,
    first_device,
    created_at,
    cast(created_at as date) as created_date,
    _loaded_at
from source
qualify row_number() over (partition by customer_id order by _loaded_at desc) = 1
