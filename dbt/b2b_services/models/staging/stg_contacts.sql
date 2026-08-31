with source as (

    select * from {{ source('crm', 'contacts') }}

)

select
    contact_id,
    account_id,
    full_name,
    email,
    title,
    created_at,
    _loaded_at
from source
qualify row_number() over (partition by contact_id order by _loaded_at desc) = 1
