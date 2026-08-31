with source as (

    select * from {{ source('shop_db', 'returns') }}

)

select
    return_id,
    order_id,
    order_item_id,
    sku_id,
    category,
    quantity,
    reason,
    refund_amount,
    requested_at,
    cast(requested_at as date) as request_date,
    refunded_at,
    _loaded_at
from source
qualify row_number() over (partition by return_id order by _loaded_at desc) = 1
