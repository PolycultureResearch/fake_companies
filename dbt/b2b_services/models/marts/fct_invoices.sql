-- One row per invoice. Grain: invoice_id.
select
    i.invoice_id,
    i.contract_id,
    i.account_id,
    i.kind,
    i.amount,
    i.status,
    i.issued_at,
    i.issued_date,
    i.due_at
from {{ ref('stg_invoices') }} as i
