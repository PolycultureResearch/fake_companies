with source as (

    select * from {{ source('fulfillment', 'shipments') }}

)

select
    shipment_id,
    order_id,
    carrier,
    status,
    placed_at,
    shipped_at,
    delivered_at,
    _loaded_at
from source
qualify row_number() over (partition by shipment_id order by _loaded_at desc) = 1
