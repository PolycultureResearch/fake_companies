with source as (

    select * from {{ source('pos', 'scan_sales') }}

)

select
    scan_id,
    week_start,
    retailer_banner,
    channel_type,
    product_id,
    category,
    units,
    dollars,
    currency,
    _loaded_at
from source
qualify row_number() over (partition by scan_id order by _loaded_at desc) = 1
