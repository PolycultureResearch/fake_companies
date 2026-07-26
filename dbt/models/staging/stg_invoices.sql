with source as (

    select * from {{ source('billing', 'invoices') }}

)

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
    cast(issued_at as date) as issued_date,
    _loaded_at
from source
qualify row_number() over (partition by invoice_id order by _loaded_at desc) = 1
