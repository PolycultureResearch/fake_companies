with source as (

    select * from {{ source('payments', 'transactions') }}

)

select
    payment_id,
    order_id,
    kind,
    amount,
    currency,
    payment_method,
    status,
    failure_code,
    created_at,
    cast(created_at as date) as transaction_date,
    _loaded_at
from source
qualify row_number() over (partition by payment_id order by _loaded_at desc) = 1
