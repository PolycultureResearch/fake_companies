"""The drivers added for the canonical trees' scenarios, each against its null twin.

- retail ``price.<category>``: a list-price change on one category's lines;
- wholesale ``price.<category>``: the case price and the shelf price, with
  POS answering through the category's elasticity;
- wholesale ``ordering.<channel_type>``: a destock, sell-in down with
  sell-through untouched;
- B2B ``delivery_time_scale``: balance invoices later, bookings untouched.

Each driver is exactly 1.0 when no anomaly moves it, which is what keeps the
default bytes (the golden suite pins B2C; the other verticals' default output
was checked unchanged when these were added). The twin comparison below
shows each one moves what it says and nothing upstream of it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.config.schema import ScriptedAnomaly, Window
from fake_companies.generate import generate

ROOT = Path(__file__).resolve().parents[1]


def _with(config: str, anomaly: ScriptedAnomaly):
    cfg = load_config(ROOT / "configs" / config)
    anomalies = cfg.anomalies.model_copy(update={"scripted": [anomaly], "surprise": None})
    cfg = cfg.model_copy(update={"anomalies": anomalies})
    return generate(cfg, anomalies="none"), generate(cfg, anomalies=f"only:{anomaly.name}")


def _day(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize()


@pytest.mark.slow
def test_retail_price_change_moves_that_categorys_price_and_mix_not_units():
    start, end = dt.date(2024, 2, 5), dt.date(2024, 2, 25)
    a = ScriptedAnomaly(
        name="pants_price_up",
        kind="rate",
        type="level_shift",
        target="price.pants",
        window=Window(start=start, end=end),
        magnitude=1.15,
    )
    null, world = _with("smoke_retail_90d.yaml", a)
    rec = next(r for r in world.ground_truth if r.id == a.name)
    assert (rec.root_metric, rec.root_slice) == ("avg_unit_price", {"category": "pants"})

    products = null.frames["shop_db.products"].set_index("sku_id")["price"]
    w_items = world.frames["shop_db.order_items"]
    n_items = null.frames["shop_db.order_items"]
    # Lines per order and quantities do not read price: total units are exact.
    assert w_items["quantity"].sum() == n_items["quantity"].sum()

    day = _day(w_items["placed_at"])
    inside = (day >= pd.Timestamp(start)) & (day <= pd.Timestamp(end))
    ratio = w_items["unit_price"] / w_items["sku_id"].map(products)
    pants = w_items["category"] == "pants"
    assert np.allclose(ratio[pants & inside], 1.15)
    assert np.allclose(ratio[~(pants & inside)], 1.0)

    # Elasticity 1.5: the pants share of units falls in the window.
    def share(items):
        d = _day(items["placed_at"])
        win = items[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]
        return win.loc[win["category"] == "pants", "quantity"].sum() / win["quantity"].sum()

    assert share(w_items) < share(n_items) * 0.92


@pytest.mark.slow
def test_wholesale_price_change_moves_case_and_shelf_price_and_volume():
    start, end = dt.date(2024, 2, 5), dt.date(2024, 3, 3)
    a = ScriptedAnomaly(
        name="teas_list_price_up",
        kind="rate",
        type="level_shift",
        target="price.teas",
        window=Window(start=start, end=end),
        magnitude=1.10,
    )
    null, world = _with("smoke_cpg_90d.yaml", a)
    rec = next(r for r in world.ground_truth if r.id == a.name)
    assert (rec.root_metric, rec.root_slice) == ("avg_case_price", {"category": "teas"})

    ns, ws = null.frames["erp.shipments"], world.frames["erp.shipments"]
    teas_w = ws[ws["category"] == "teas"]
    base = null.frames["erp.products"].set_index("product_id")["case_price"]
    ratio = teas_w["case_price"] / teas_w["product_id"].map(base)
    # Priced at the ordering week; a few shipments land more than a week after
    # it, so judge from ten days in.
    shipped = _day(teas_w["shipped_at"])
    in_ship = (shipped >= pd.Timestamp(start) + pd.Timedelta(days=10)) & (
        shipped <= pd.Timestamp(end)
    )
    assert np.isclose(ratio, 1.0).sum() + np.isclose(ratio, 1.10).sum() == len(ratio)
    assert np.allclose(ratio[in_ship], 1.10)
    assert np.allclose(
        ws.loc[ws["category"] != "teas", "case_price"].to_numpy().mean(),
        ns.loc[ns["category"] != "teas", "case_price"].to_numpy().mean(),
    )

    def teas_pos(result):
        scan = result.frames["pos.scan_sales"]
        wk = pd.to_datetime(scan["week_start"])
        win = scan[(wk >= pd.Timestamp(start)) & (wk <= pd.Timestamp(end) - pd.Timedelta(days=6))]
        teas = win[win["category"] == "teas"]
        return teas["units"].sum(), teas["dollars"].sum() / teas["units"].sum()

    (n_units, n_price), (w_units, w_price) = teas_pos(null), teas_pos(world)
    # Elasticity 1.2: 1.10 ** -1.2 = 0.892 of the volume, at 1.10 the shelf price.
    assert 0.85 < w_units / n_units < 0.94
    assert w_price / n_price == pytest.approx(1.10, rel=0.02)


@pytest.mark.slow
def test_wholesale_destock_moves_sell_in_not_sell_through():
    start, end = dt.date(2024, 2, 5), dt.date(2024, 3, 3)
    a = ScriptedAnomaly(
        name="grocery_destock",
        kind="rate",
        type="level_shift",
        target="ordering.grocery",
        window=Window(start=start, end=end),
        magnitude=0.6,
    )
    null, world = _with("smoke_cpg_90d.yaml", a)
    rec = next(r for r in world.ground_truth if r.id == a.name)
    assert (rec.root_metric, rec.root_slice) == ("shipped_cases", {"channel_type": "grocery"})
    pd.testing.assert_frame_equal(null.frames["pos.scan_sales"], world.frames["pos.scan_sales"])

    def cases(result, channel):
        s = result.frames["erp.shipments"]
        d = _day(s["shipped_at"])
        win = s[(d >= pd.Timestamp(start) + pd.Timedelta(days=7)) & (d <= pd.Timestamp(end))]
        return win.loc[win["channel_type"] == channel, "cases"].sum()

    assert cases(world, "grocery") / cases(null, "grocery") == pytest.approx(0.6, abs=0.05)
    assert cases(world, "natural") == cases(null, "natural")


@pytest.mark.slow
def test_b2b_delivery_slowdown_delays_balance_invoices_only():
    cfg = load_config(ROOT / "configs" / "smoke_b2b_90d.yaml")
    start = cfg.timeline.start + dt.timedelta(days=20)
    end = start + dt.timedelta(days=27)
    a = ScriptedAnomaly(
        name="delivery_crunch",
        kind="rate",
        type="level_shift",
        target="delivery_time_scale",
        window=Window(start=start, end=end),
        magnitude=1.6,
    )
    null, world = _with("smoke_b2b_90d.yaml", a)
    rec = next(r for r in world.ground_truth if r.id == a.name)
    assert rec.root_metric == "invoiced_revenue"
    pd.testing.assert_frame_equal(null.frames["crm.deals"], world.frames["crm.deals"])

    nc, wc = null.frames["billing.contracts"], world.frames["billing.contracts"]
    pd.testing.assert_series_equal(nc["value"], wc["value"])
    signed = _day(wc["signed_at"])
    hit = (signed >= pd.Timestamp(start)) & (signed <= pd.Timestamp(end))
    assert hit.any()
    days_n = (pd.to_datetime(nc["delivery_at"]) - pd.to_datetime(nc["signed_at"])).dt.days
    days_w = (pd.to_datetime(wc["delivery_at"]) - pd.to_datetime(wc["signed_at"])).dt.days
    both = days_n.notna() & days_w.notna()
    assert (days_w[hit & both] >= days_n[hit & both]).all()
    assert (days_w[hit & both] > days_n[hit & both]).mean() > 0.9
    assert days_w[~hit & both].equals(days_n[~hit & both])

    ni, wi = null.frames["billing.invoices"], world.frames["billing.invoices"]
    up_n = ni[ni["kind"] == "upfront"]["amount"].sum()
    up_w = wi[wi["kind"] == "upfront"]["amount"].sum()
    assert up_n == up_w
