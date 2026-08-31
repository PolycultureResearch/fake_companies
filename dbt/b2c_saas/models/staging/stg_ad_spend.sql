with source as (

    select * from {{ source('ad_platform', 'ad_spend') }}

)

select
    spend_id,
    cast(date as date)      as spend_date,
    channel,
    campaign_id,
    impressions,
    clicks,
    spend,
    currency,
    _loaded_at
from source
qualify row_number() over (partition by spend_id order by _loaded_at desc) = 1
