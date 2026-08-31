from __future__ import annotations

import numpy as np

from fake_companies.core import RngHub, build_calendar
from fake_companies.verticals.retail_dtc.entities.catalog import build_catalog


def test_catalog_matches_config(retail_cfg, retail_result):
    products = retail_result.frames["shop_db.products"]
    for cat, spec in retail_cfg.catalog.categories.items():
        sub = products[products["category"] == cat]
        assert len(sub) == spec.n_skus
        assert (sub["price"] >= spec.price_min).all()
        assert (sub["price"] <= spec.price_max + 1).all()
        assert (sub["unit_cost"] < sub["price"]).all()
        assert set(sub["size"]) <= set(spec.sizes)
    # Retail price endings.
    cents = np.round((products["price"] % 1) * 100).astype(int)
    assert (cents == 95).all()
    assert products["sku_id"].is_unique
    assert products["sku"].is_unique


def test_catalog_deterministic(retail_cfg):
    cal = build_calendar(retail_cfg)
    f1, _ = build_catalog(retail_cfg, cal, RngHub(retail_cfg.seed))
    f2, _ = build_catalog(retail_cfg, cal, RngHub(retail_cfg.seed))
    assert f1.equals(f2)
