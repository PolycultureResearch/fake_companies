with source as (

    select * from {{ source('shop_db', 'orders') }}

)

select
    order_id,
    customer_id,
    session_id,
    channel,
    country,
    device,
    is_first_order,
    discount_code,
    item_count,
    gross_amount,
    discount_amount,
    shipping_amount,
    total_amount,
    currency,
    placed_at,
    cast(placed_at as date) as order_date,
    _loaded_at
from source
qualify row_number() over (partition by order_id order by _loaded_at desc) = 1
