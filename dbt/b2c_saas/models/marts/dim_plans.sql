select
    plan_id,
    plan_name,
    billing_period,
    price,
    monthly_price,
    currency
from {{ ref('stg_plans') }}
