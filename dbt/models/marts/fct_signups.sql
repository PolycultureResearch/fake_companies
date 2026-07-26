-- One row per user signup. Grain: user_id.
select
    u.user_id,
    u.signup_date,
    u.created_at,
    u.signup_channel,
    u.country,
    u.device_at_signup as device
from {{ ref('stg_users') }} as u
