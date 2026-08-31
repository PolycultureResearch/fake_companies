select
    user_id,
    email,
    full_name,
    country,
    signup_channel,
    device_at_signup,
    created_at,
    signup_date
from {{ ref('stg_users') }}
