with source as (

    select * from {{ source('erp', 'accounts') }}

)

select
    account_id,
    name,
    kind,
    channel_type,
    banner,
    region,
    _loaded_at
from source
qualify row_number() over (partition by account_id order by _loaded_at desc) = 1
