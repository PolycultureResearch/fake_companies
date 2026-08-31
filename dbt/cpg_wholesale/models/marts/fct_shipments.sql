-- One row per shipment line. Grain: shipment_id.
select
    s.shipment_id,
    s.account_id,
    s.product_id,
    s.category,
    s.channel_type,
    s.cases,
    s.case_price,
    s.gross_amount,
    s.discount_amount,
    s.net_amount,
    s.shipped_at,
    s.shipped_date
from {{ ref('stg_shipments') }} as s
