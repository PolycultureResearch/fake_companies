with source as (

    select * from {{ source('crm', 'deal_stage_events') }}

)

select
    event_id,
    deal_id,
    account_id,
    stage,
    occurred_at,
    cast(occurred_at as date) as occurred_date,
    _loaded_at
from source
qualify row_number() over (partition by event_id order by _loaded_at desc) = 1
