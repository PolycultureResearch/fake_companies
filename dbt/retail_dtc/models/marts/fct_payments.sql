-- One row per payment transaction (charge or refund). Grain: payment_id.
select
    t.payment_id,
    t.order_id,
    t.kind,
    t.amount,
    t.currency,
    t.payment_method,
    t.status,
    t.failure_code,
    t.transaction_date,
    t.created_at
from {{ ref('stg_transactions') }} as t
