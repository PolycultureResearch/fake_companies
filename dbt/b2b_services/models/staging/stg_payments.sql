with source as (

    select * from {{ source('billing', 'payments') }}

)

select
    payment_id,
    invoice_id,
    account_id,
    amount,
    currency,
    method,
    paid_at,
    cast(paid_at as date) as paid_date,
    _loaded_at
from source
qualify row_number() over (partition by payment_id order by _loaded_at desc) = 1
