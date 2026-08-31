with source as (

    select * from {{ source('crm', 'deals') }}

)

select
    deal_id,
    account_id,
    contact_id,
    source,
    industry,
    size_tier,
    region,
    stage,
    amount,
    currency,
    created_at,
    cast(created_at as date) as created_date,
    closed_at,
    cast(closed_at as date) as closed_date,
    _loaded_at
from source
qualify row_number() over (partition by deal_id order by _loaded_at desc) = 1
