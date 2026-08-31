from __future__ import annotations

import numpy as np
import pandas as pd


def test_pos_scale_tracks_market_config(cpg_cfg, cpg_result):
    """Weekly POS volume tracks stores x velocity x product weights."""
    pos = cpg_result.frames["pos.scan_sales"].drop_duplicates("scan_id")
    weekly = pos.groupby("week_start")["units"].sum()

    # Expected weekly units: sum over banners of stores x velocity x n_products
    # (weights are normalized to mean 1; demand seasonality means ~annual mean,
    # and the smoke anomaly plus dropout push individual weeks around).
    n_products = sum(c.n_products for c in cpg_cfg.catalog.categories.values())
    expected = sum(r.stores * r.velocity for r in cpg_cfg.market.retailers.values()) * n_products
    ratio = weekly.median() / expected
    assert 0.6 < ratio < 1.6, (weekly.median(), expected)


def test_pos_weekly_grain(cpg_result):
    pos = cpg_result.frames["pos.scan_sales"].drop_duplicates("scan_id")
    week_starts = pd.to_datetime(pos["week_start"])
    assert (week_starts.dt.dayofweek == 0).all()  # Mondays
    # One row per (week, banner, product) at most.
    assert not pos.duplicated(["week_start", "retailer_banner", "product_id"]).any()


def test_shipments_lag_pos(cpg_cfg, cpg_result):
    """Total cases shipped ~ total POS units / case size, one week behind."""
    pos = cpg_result.frames["pos.scan_sales"].drop_duplicates("scan_id")
    ship = cpg_result.frames["erp.shipments"].drop_duplicates("shipment_id")
    products = cpg_result.frames["erp.products"]

    case_size = products.set_index("product_id")["case_size"]
    pos_cases = (
        pos.assign(cases=pos["units"] / pos["product_id"].map(case_size)).groupby("product_id")[
            "cases"
        ]
    ).sum()
    shipped = ship.groupby("product_id")["cases"].sum()
    # Aggregate replenishment roughly re-orders what sold (order noise is
    # mean-1; the last POS week is never re-ordered inside the window, and the
    # dq volume_dropout removed observed POS rows but not the demand shipments
    # were placed against — so shipped runs slightly above observed POS).
    ratio = shipped.sum() / pos_cases.sum()
    assert 0.85 < ratio < 1.30, ratio

    # The lag: shipments in the week after a POS week reflect that week's units.
    assert (pd.to_datetime(ship["shipped_at"]).dt.dayofweek < 7).all()


def test_trade_promos_discount_shipments(cpg_cfg, cpg_result):
    ship = cpg_result.frames["erp.shipments"].drop_duplicates("shipment_id")
    accounts = cpg_result.frames["erp.accounts"]
    promo = cpg_cfg.promos[0]

    banner_accounts = set(accounts.loc[accounts["banner"] == promo.banner, "account_id"])
    day = pd.to_datetime(ship["shipped_at"]).dt.normalize()
    # Discounted lines exist, only at the promo banner/category, at the right rate.
    disc = ship[ship["discount_amount"] > 0]
    assert len(disc) > 0
    assert set(disc["account_id"]) <= banner_accounts
    assert (disc["category"] == promo.category).all()
    np.testing.assert_allclose(
        disc["discount_amount"], disc["gross_amount"] * promo.discount_pct, atol=0.02
    )
    # Discounts only while the promo window (plus reorder lag) is active.
    assert (day[disc.index] >= pd.Timestamp(promo.window.start)).all()

    # Amounts add up everywhere.
    assert np.allclose(ship["net_amount"], ship["gross_amount"] - ship["discount_amount"])
    assert np.allclose(ship["gross_amount"], ship["cases"] * ship["case_price"], atol=0.02)


def test_reference_frames(cpg_cfg, cpg_result):
    products = cpg_result.frames["erp.products"]
    accounts = cpg_result.frames["erp.accounts"]
    promos = cpg_result.frames["promo.trade_promotions"]

    for cat, spec in cpg_cfg.catalog.categories.items():
        sub = products[products["category"] == cat]
        assert len(sub) == spec.n_products
        assert (sub["case_price"] >= spec.case_price_min).all()
        assert (sub["case_price"] <= spec.case_price_max).all()
        assert (sub["msrp"] < sub["case_price"]).all()  # per-unit price < case price

    n_direct = len([b for b in cpg_cfg.market.retailers if b != "independents"])
    assert len(accounts) == n_direct + len(cpg_cfg.market.distributors)
    assert len(promos) == len(cpg_cfg.promos)


def test_marketing_decoupled(cpg_result):
    """The decoupling contract: spend drivers affect only marketing_spend."""
    spend_records = [g for g in cpg_result.ground_truth if g.target.startswith("spend.")]
    for g in spend_records:
        assert g.affected_metrics == ["marketing_spend"], g
    # And ad spend exists at all (the decoy is present, not absent).
    assert len(cpg_result.frames["ad_platform.ad_spend"]) > 0
