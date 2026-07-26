with source as (

    select * from {{ source('app_db', 'plans') }}

)

select
    plan_id,
    name as plan_name,
    billing_period,
    price,
    -- monthly-equivalent price so annual plans are comparable to monthly MRR
    case
        when billing_period = 'annual' then price / 12.0
        else price
    end as monthly_price,
    currency,
    _loaded_at
from source
qualify row_number() over (partition by plan_id order by _loaded_at desc) = 1
