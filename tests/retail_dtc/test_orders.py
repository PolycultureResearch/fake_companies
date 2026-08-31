from __future__ import annotations

import numpy as np
import pandas as pd


def test_first_order_conversion_within_ci(retail_cfg, retail_result):
    """Realized session->order conversion tracks driver x device multipliers."""
    sessions = retail_result.frames["web.sessions"]
    orders = retail_result.frames["shop_db.orders"]
    firsts = orders[orders["is_first_order"]].drop_duplicates("order_id")

    # Expected conversions: mean order_rate x device multiplier over sessions,
    # excluding the scripted mobile drop window (tested separately).
    dev_mult = retail_cfg.conversion.device_multipliers
    expected = 0.0
    for ch, ch_sessions in sessions.groupby("channel"):
        base = retail_cfg.conversion.order_rate.get(ch, retail_cfg.conversion.order_rate_default)
        mults = ch_sessions["device"].map(lambda d: dev_mult.get(d, 1.0))
        expected += float((base * mults).sum())

    realized = len(firsts)
    sd = np.sqrt(expected)  # ~Poisson-binomial; anomaly makes realized slightly lower
    assert expected - 6 * sd < realized < expected + 4 * sd, (realized, expected)


def test_order_amounts_add_up(retail_result):
    orders = retail_result.frames["shop_db.orders"]
    items = retail_result.frames["shop_db.order_items"]

    # Recompute gross/units from lines (skip dq-duplicated order rows).
    o = orders.drop_duplicates("order_id").set_index("order_id")
    line_gross = items.groupby("order_id")["line_amount"].sum()
    line_units = items.groupby("order_id")["quantity"].sum()

    joined = o.join(line_gross.rename("g")).join(line_units.rename("u"))
    assert np.allclose(joined["gross_amount"], joined["g"], atol=0.01)
    assert (joined["item_count"] == joined["u"]).all()
    total = joined["gross_amount"] - joined["discount_amount"] + joined["shipping_amount"]
    assert np.allclose(joined["total_amount"], total, atol=0.01)
    # Discounts only with a code, and free shipping above the threshold.
    assert (joined.loc[joined["discount_amount"] > 0, "discount_code"].notna()).all()


def test_lines_per_order_tracks_driver(retail_cfg, retail_result):
    orders = retail_result.frames["shop_db.orders"].drop_duplicates("order_id")
    items = retail_result.frames["shop_db.order_items"]
    lines_per_order = len(items) / len(orders)
    mean = retail_cfg.basket.items_per_order_mean
    assert abs(lines_per_order - mean) < 0.15, (lines_per_order, mean)


def test_repeat_orders_shape(retail_cfg, retail_result):
    orders = retail_result.frames["shop_db.orders"].drop_duplicates("order_id")
    customers = retail_result.frames["shop_db.customers"]
    repeats = orders[~orders["is_first_order"]]
    assert len(repeats) > 0
    assert repeats["session_id"].isna().all()
    assert set(repeats["channel"]) <= set(retail_cfg.repeat.channel_mix)
    # A repeat order never precedes the customer's first order.
    created = customers.set_index("customer_id")["created_at"]
    first_at = pd.to_datetime(repeats["customer_id"].map(created))
    assert (pd.to_datetime(repeats["placed_at"]).to_numpy() > first_at.to_numpy()).all()


def test_category_mix_tracks_popularity(retail_cfg, retail_result):
    items = retail_result.frames["shop_db.order_items"]
    counts = items["category"].value_counts(normalize=True)
    pops = {c: s.popularity for c, s in retail_cfg.catalog.categories.items()}
    total = sum(pops.values())
    for cat, pop in pops.items():
        assert abs(counts.get(cat, 0.0) - pop / total) < 0.05
