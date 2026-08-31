with source as (

    select * from {{ source('billing', 'payments') }}

)

select
    payment_id,
    invoice_id,
    user_id,
    amount,
    currency,
    payment_method,
    status,
    failure_code,
    created_at,
    cast(created_at as date) as payment_date,
    _loaded_at
from source
-- Raw sources may carry a duplicate_rows DQ corruption; keep one row per PK so
-- the modeled layer is clean. (Tremor profiles the raw tables for that anomaly.)
qualify row_number() over (partition by payment_id order by _loaded_at desc) = 1
