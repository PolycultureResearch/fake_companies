-- One row per payment. Grain: payment_id.
select
    p.payment_id,
    p.invoice_id,
    p.account_id,
    p.amount,
    p.method,
    p.paid_at,
    p.paid_date
from {{ ref('stg_payments') }} as p
