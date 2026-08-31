-- One row per order, enriched with fulfillment timing. Grain: order_id.
select
    o.order_id,
    o.customer_id,
    o.session_id,
    o.order_date,
    o.placed_at,
    o.channel,
    o.country,
    o.device,
    o.is_first_order,
    o.discount_code,
    o.item_count,
    o.gross_amount,
    o.discount_amount,
    o.shipping_amount,
    o.total_amount,
    s.carrier,
    s.status as fulfillment_status,
    s.shipped_at,
    s.delivered_at,
    date_diff('day', o.placed_at, s.delivered_at) as fulfillment_days
from {{ ref('stg_orders') }} as o
left join {{ ref('stg_shipments') }} as s
    on o.order_id = s.order_id
