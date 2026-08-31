-- One row per SKU. Grain: sku_id.
select
    p.sku_id,
    p.sku,
    p.product_name,
    p.category,
    p.color,
    p.size,
    p.price,
    p.unit_cost,
    p.currency
from {{ ref('stg_products') }} as p
