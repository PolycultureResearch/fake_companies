with source as (

    select * from {{ source('crm', 'accounts') }}

)

select
    account_id,
    name,
    industry,
    size_tier,
    region,
    created_at,
    cast(created_at as date) as created_date,
    _loaded_at
from source
qualify row_number() over (partition by account_id order by _loaded_at desc) = 1
