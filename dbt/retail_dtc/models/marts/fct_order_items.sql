-- One row per order line, with product cost for margin. Grain: order_item_id.
-- margin_amount is merchandise margin (line revenue - unit cost x qty);
-- order-level discounts are accounted in net_revenue, not here.
select
    i.order_item_id,
    i.order_id,
    i.sku_id,
    i.category,
    i.quantity,
    i.unit_price,
    i.line_amount,
    i.order_date,
    i.placed_at,
    p.unit_cost,
    i.line_amount - (i.quantity * p.unit_cost) as margin_amount
from {{ ref('stg_order_items') }} as i
left join {{ ref('dim_products') }} as p
    on i.sku_id = p.sku_id
