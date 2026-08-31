-- One row per (week, banner, product) scan record. Grain: scan_id.
-- Weekly-grain data keyed to the week_start day for metric_time.
select
    x.scan_id,
    x.week_start,
    x.retailer_banner,
    x.channel_type,
    x.product_id,
    x.category,
    x.units,
    x.dollars
from {{ ref('stg_scan_sales') }} as x
