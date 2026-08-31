with source as (

    select * from {{ source('shop_db', 'products') }}

)

select
    sku_id,
    sku,
    product_name,
    category,
    color,
    size,
    price,
    unit_cost,
    currency,
    _loaded_at
from source
qualify row_number() over (partition by sku_id order by _loaded_at desc) = 1
