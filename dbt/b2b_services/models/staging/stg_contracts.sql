with source as (

    select * from {{ source('billing', 'contracts') }}

)

select
    contract_id,
    deal_id,
    account_id,
    value,
    currency,
    signed_at,
    cast(signed_at as date) as signed_date,
    delivery_at,
    _loaded_at
from source
qualify row_number() over (partition by contract_id order by _loaded_at desc) = 1
