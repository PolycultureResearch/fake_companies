with source as (

    select * from {{ source('erp', 'products') }}

)

select
    product_id,
    sku,
    product_name,
    category,
    case_size,
    case_price,
    msrp,
    currency,
    _loaded_at
from source
qualify row_number() over (partition by product_id order by _loaded_at desc) = 1
