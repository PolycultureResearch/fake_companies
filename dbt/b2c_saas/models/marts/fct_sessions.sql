-- One row per web session. Grain: session_id.
select
    s.session_id,
    s.user_id,
    s.session_date,
    s.started_at,
    s.channel,
    s.country,
    s.device,
    s.utm_campaign,
    s.duration_seconds,
    s.page_views,
    s.landing_page
from {{ ref('stg_sessions') }} as s
