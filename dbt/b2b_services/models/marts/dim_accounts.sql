-- One row per account. Grain: account_id.
select
    a.account_id,
    a.name,
    a.industry,
    a.size_tier,
    a.region,
    a.created_at,
    a.created_date
from {{ ref('stg_accounts') }} as a
