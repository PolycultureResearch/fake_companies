-- One row per product. Grain: product_id.
select
    p.product_id,
    p.sku,
    p.product_name,
    p.category,
    p.case_size,
    p.case_price,
    p.msrp
from {{ ref('stg_products') }} as p
