-- One row per trading partner. Grain: account_id.
select
    a.account_id,
    a.name,
    a.kind,
    a.channel_type,
    a.banner,
    a.region
from {{ ref('stg_accounts') }} as a
