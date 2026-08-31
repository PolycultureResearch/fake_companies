-- One row per customer. Grain: customer_id.
select
    c.customer_id,
    c.email,
    c.full_name,
    c.country,
    c.first_channel,
    c.first_device,
    c.created_at,
    c.created_date,
    cast(date_trunc('month', c.created_date) as date) as cohort_month
from {{ ref('stg_customers') }} as c
