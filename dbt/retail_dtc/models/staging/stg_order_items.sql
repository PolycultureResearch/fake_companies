with source as (

    select * from {{ source('shop_db', 'order_items') }}

)

select
    order_item_id,
    order_id,
    sku_id,
    category,
    quantity,
    unit_price,
    line_amount,
    placed_at,
    cast(placed_at as date) as order_date,
    _loaded_at
from source
qualify row_number() over (partition by order_item_id order by _loaded_at desc) = 1
