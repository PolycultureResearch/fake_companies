with source as (

    select * from {{ source('erp', 'shipments') }}

)

select
    shipment_id,
    account_id,
    product_id,
    category,
    channel_type,
    cases,
    case_price,
    gross_amount,
    discount_amount,
    net_amount,
    currency,
    shipped_at,
    cast(shipped_at as date) as shipped_date,
    _loaded_at
from source
qualify row_number() over (partition by shipment_id order by _loaded_at desc) = 1
