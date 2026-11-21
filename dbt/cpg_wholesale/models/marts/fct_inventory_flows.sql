-- Units moving into and out of retail inventory. Grain: one flow row.
--
-- The ERP ships cases to each account (sell-in); the POS feed reports units
-- each banner sold (sell-through). What retailers hold is the running balance
-- of the two: `trade_inventory_build` sums these flows from the first scan
-- week on. The opening stock is unknown, so the level is the build since the
-- data starts (negative when retailers have destocked), not an absolute count.
--
-- POS units arrive weekly, keyed to the Monday. They are spread evenly over
-- their seven days here, or the daily balance would drop by a whole week of
-- sales every Monday and climb back as the week's shipments land.
-- Shipments outside the scanned weeks are left out, so the two sides cover
-- the same days.
with weeks as (

    select min(week_start) as first_day, max(week_start) + 6 as last_day
    from {{ ref('stg_scan_sales') }}

),

sell_in as (

    select
        'ship-' || s.shipment_id as flow_id,
        s.shipped_date as flow_date,
        a.banner as retailer_banner,
        s.category,
        cast(s.cases * p.case_size as double) as units
    from {{ ref('stg_shipments') }} as s
    inner join {{ ref('stg_accounts') }} as a on s.account_id = a.account_id
    inner join {{ ref('stg_products') }} as p on s.product_id = p.product_id
    cross join weeks as w
    where s.shipped_date between w.first_day and w.last_day

),

sell_through as (

    select
        'scan-' || x.scan_id || '-' || cast(d.k as varchar) as flow_id,
        x.week_start + cast(d.k as integer) as flow_date,
        x.retailer_banner,
        x.category,
        -cast(x.units as double) / 7 as units
    from {{ ref('stg_scan_sales') }} as x
    cross join (select unnest(range(7)) as k) as d

)

select flow_id, flow_date, retailer_banner, category, units from sell_in
union all
select flow_id, flow_date, retailer_banner, category, units from sell_through
