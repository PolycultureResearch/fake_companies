from __future__ import annotations

import numpy as np
import pandas as pd


def test_shipment_timestamps_monotone_and_censored(retail_result):
    ship = retail_result.frames["fulfillment.shipments"]
    cal = retail_result.calendar
    cutoff = pd.Timestamp(cal.end) + pd.Timedelta(days=1)

    placed = pd.to_datetime(ship["placed_at"])
    shipped = pd.to_datetime(ship["shipped_at"])
    delivered = pd.to_datetime(ship["delivered_at"])

    m = shipped.notna()
    assert (shipped[m] > placed[m]).all()
    m2 = delivered.notna()
    assert (delivered[m2] > shipped[m2]).all()
    assert (shipped.dropna() < cutoff).all()
    assert (delivered.dropna() < cutoff).all()

    # Status agrees with how far the shipment got.
    assert (ship.loc[delivered.notna(), "status"] == "delivered").all()
    assert (ship.loc[shipped.notna() & delivered.isna(), "status"] == "shipped").all()
    assert (ship.loc[shipped.isna(), "status"] == "pending").all()


def test_returns_follow_delivery(retail_result):
    ret = retail_result.frames["shop_db.returns"]
    ship = retail_result.frames["fulfillment.shipments"]
    items = retail_result.frames["shop_db.order_items"]
    cal = retail_result.calendar
    cutoff = pd.Timestamp(cal.end) + pd.Timedelta(days=1)

    assert len(ret) > 0
    delivered = ship.set_index("order_id")["delivered_at"]
    req = pd.to_datetime(ret["requested_at"])
    dlv = pd.to_datetime(ret["order_id"].map(delivered))
    assert dlv.notna().all()  # only delivered orders produce returns
    assert (req > dlv).all()
    assert (req < cutoff).all()

    refunded = pd.to_datetime(ret["refunded_at"])
    m = refunded.notna()
    assert (refunded[m] > req[m]).all()

    # Refund never exceeds the line amount.
    line_amount = items.set_index("order_item_id")["line_amount"]
    assert (ret["refund_amount"] <= ret["order_item_id"].map(line_amount) + 0.01).all()


def test_return_rates_per_category_within_ci(retail_cfg, retail_result):
    """Realized per-category return share of delivered lines tracks config."""
    ret = retail_result.frames["shop_db.returns"]
    items = retail_result.frames["shop_db.order_items"]
    ship = retail_result.frames["fulfillment.shipments"]

    delivered_orders = set(ship.loc[ship["delivered_at"].notna(), "order_id"])
    eligible = items[items["order_id"].isin(delivered_orders)]

    for cat in retail_cfg.catalog.categories:
        n = len(eligible[eligible["category"] == cat])
        if n < 200:
            continue
        k = len(ret[ret["category"] == cat])
        p = retail_cfg.returns.rate.get(cat, retail_cfg.returns.rate_default)
        # Right-censoring near the timeline end loses some requests: allow the
        # realized rate to run below p but bound it with a generous CI.
        sd = np.sqrt(n * p * (1 - p))
        assert k < n * p + 4 * sd, (cat, k, n * p)
        assert k > n * p * 0.5 - 4 * sd, (cat, k, n * p)


def test_transactions_cover_orders_and_refunds(retail_cfg, retail_result):
    tx = retail_result.frames["payments.transactions"]
    orders = retail_result.frames["shop_db.orders"].drop_duplicates("order_id")
    ret = retail_result.frames["shop_db.returns"]

    charges_ok = tx[(tx["kind"] == "charge") & (tx["status"] == "succeeded")]
    assert set(orders["order_id"]) == set(charges_ok["order_id"])

    # Every succeeded charge matches its order total.
    totals = orders.set_index("order_id")["total_amount"]
    assert np.allclose(charges_ok["order_id"].map(totals), charges_ok["amount"], atol=0.01)

    failed = tx[tx["status"] == "failed"]
    frac = len(failed) / len(orders)
    p = retail_cfg.payments.failure_rate
    assert abs(frac - p) < 4 * np.sqrt(p / len(orders)) + 0.01
    assert failed["failure_code"].notna().all()

    refunds = tx[tx["kind"] == "refund"]
    n_refunded = int(ret["refunded_at"].notna().sum())
    assert len(refunds) == n_refunded
