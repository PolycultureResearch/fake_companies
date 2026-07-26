with source as (

    select * from {{ source('app_db', 'subscriptions') }}

)

select
    subscription_id,
    user_id,
    plan_id,
    status,
    trial_start_at,
    trial_end_at,
    started_at,
    canceled_at,
    _loaded_at
from source
qualify row_number() over (partition by subscription_id order by _loaded_at desc) = 1
