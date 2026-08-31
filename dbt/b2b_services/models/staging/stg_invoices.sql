with source as (

    select * from {{ source('billing', 'invoices') }}

)

select
    invoice_id,
    contract_id,
    account_id,
    kind,
    amount,
    currency,
    status,
    issued_at,
    cast(issued_at as date) as issued_date,
    due_at,
    _loaded_at
from source
qualify row_number() over (partition by invoice_id order by _loaded_at desc) = 1
