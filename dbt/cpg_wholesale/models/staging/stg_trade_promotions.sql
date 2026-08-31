with source as (

    select * from {{ source('promo', 'trade_promotions') }}

)

select
    promo_id,
    name,
    retailer_banner,
    category,
    start_date,
    end_date,
    discount_pct,
    _loaded_at
from source
qualify row_number() over (partition by promo_id order by _loaded_at desc) = 1
