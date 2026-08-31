-- One row per payment attempt. Grain: payment_id.
-- revenue = succeeded payment amount; payment_failure_rate = failed / all.
select
    p.payment_id,
    p.invoice_id,
    p.user_id,
    p.amount,
    p.currency,
    p.payment_method,
    p.status,
    p.failure_code,
    p.created_at,
    p.payment_date,
    case when p.status = 'succeeded' then p.amount else 0.0 end as succeeded_amount,
    case when p.status = 'succeeded' then 1 else 0 end          as is_succeeded,
    case when p.status = 'failed'    then 1 else 0 end          as is_failed
from {{ ref('stg_payments') }} as p
