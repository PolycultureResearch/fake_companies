-- One row per return request. Grain: return_id.
select
    r.return_id,
    r.order_id,
    r.order_item_id,
    r.sku_id,
    r.category,
    r.quantity,
    r.reason,
    r.refund_amount,
    r.request_date,
    r.requested_at,
    r.refunded_at
from {{ ref('stg_returns') }} as r
