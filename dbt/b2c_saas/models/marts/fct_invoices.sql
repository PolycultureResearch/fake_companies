-- One row per invoice. Grain: invoice_id.
select
    invoice_id,
    subscription_id,
    user_id,
    amount,
    currency,
    period_start,
    period_end,
    status,
    issued_at,
    issued_date,
    case when status = 'paid' then 1 else 0 end as is_paid
from {{ ref('stg_invoices') }}
