"""Basket composition: ``shop_db.order_items`` + order amounts.

Per order: ``1 + Poisson(items_per_order[day] - 1)`` line items. Each line's
category is sampled from popularity x ``category_demand.<cat>[day]`` (per-day
cumulative probabilities + searchsorted, fully vectorized), the SKU uniformly
within the category. Order aggregates (item_count, gross/discount/shipping/
total) are computed FROM the lines — AOV emerges, it is never set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ..config import RetailDTCScenarioConfig as ScenarioConfig
from .catalog import SkuCatalog


def build_baskets(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    skeleton: pd.DataFrame,
    catalog: SkuCatalog,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (orders frame, order_items frame) from an id-assigned skeleton."""
    gen = rng.stream("retail_basket")
    n = len(skeleton)
    if n == 0:
        return _finalize_orders(cfg, skeleton, np.zeros(0), np.zeros(0), gen), _empty_items()

    day_idx = skeleton["day_index"].to_numpy()

    # Lines per order.
    ipo = panel.get("items_per_order")[day_idx]
    n_lines_per_order = 1 + gen.poisson(np.maximum(ipo - 1.0, 0.0))
    line_order_pos = np.repeat(np.arange(n), n_lines_per_order)
    n_lines = len(line_order_pos)
    line_day = day_idx[line_order_pos]

    # Per-day category mix: popularity x category_demand, normalized + cumsum.
    n_cats = len(catalog.categories)
    weights = np.empty((cal.n_days, n_cats))
    for ci, cat in enumerate(catalog.categories):
        weights[:, ci] = catalog.popularity[ci] * panel.get(f"category_demand.{cat}")
    probs = weights / weights.sum(axis=1, keepdims=True)
    cum = np.cumsum(probs, axis=1)

    u_cat = gen.random(n_lines)
    cat_idx = (u_cat[:, None] > cum[line_day]).sum(axis=1)
    sku_pos = catalog.sku_for_category(cat_idx, gen.random(n_lines))

    quantity = 1 + (gen.random(n_lines) < cfg.basket.extra_quantity_rate).astype(np.int64)
    unit_price = catalog.prices[sku_pos]
    line_amount = np.round(quantity * unit_price, 2)

    order_ids = skeleton["order_id"].to_numpy()
    items = pd.DataFrame(
        {
            "order_id": order_ids[line_order_pos],
            "sku_id": (sku_pos + 1).astype(np.int64),
            "category": np.asarray(catalog.categories, dtype=object)[cat_idx],
            "quantity": quantity.astype(np.int32),
            "unit_price": unit_price,
            "line_amount": line_amount,
            "placed_at": skeleton["placed_at"].to_numpy()[line_order_pos],
            "_loaded_at": pd.NaT,
        }
    )
    items.insert(0, "order_item_id", np.arange(1, n_lines + 1, dtype=np.int64))

    units = np.bincount(line_order_pos, weights=quantity, minlength=n).astype(np.int64)
    gross = np.round(np.bincount(line_order_pos, weights=line_amount, minlength=n), 2)
    orders = _finalize_orders(cfg, skeleton, units, gross, gen)
    return orders, items


def _finalize_orders(
    cfg: ScenarioConfig,
    skeleton: pd.DataFrame,
    units: np.ndarray,
    gross: np.ndarray,
    gen: np.random.Generator,
) -> pd.DataFrame:
    n = len(skeleton)
    discount = np.zeros(n)
    code = np.full(n, None, dtype=object)
    day_idx = skeleton["day_index"].to_numpy() if n else np.zeros(0, dtype=np.int64)

    placed_day = day_idx
    for promo in cfg.promos:
        # Window in day indices relative to the timeline start.
        d0 = (promo.window.start - cfg.timeline.start).days
        d1 = (promo.window.end - cfg.timeline.start).days if promo.window.end else d0
        in_win = (placed_day >= d0) & (placed_day <= d1) & (code == None)
        take = in_win & (gen.random(n) < promo.uptake)
        code[take] = promo.code
        discount[take] = np.round(gross[take] * promo.discount_pct, 2)

    merch = gross - discount
    threshold = cfg.basket.free_shipping_threshold
    if threshold is None:
        shipping = np.full(n, cfg.basket.shipping_fee)
    else:
        shipping = np.where(merch < threshold, cfg.basket.shipping_fee, 0.0)
    total = np.round(merch + shipping, 2)

    orders = skeleton.copy()
    orders["discount_code"] = code
    orders["item_count"] = units.astype(np.int32)
    orders["gross_amount"] = gross
    orders["discount_amount"] = discount
    orders["shipping_amount"] = shipping
    orders["total_amount"] = total
    orders["currency"] = cfg.company.currency
    orders["_loaded_at"] = pd.NaT
    return orders


def _empty_items() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_item_id": pd.array([], dtype="int64"),
            "order_id": pd.array([], dtype="int64"),
            "sku_id": pd.array([], dtype="int64"),
            "category": pd.array([], dtype="object"),
            "quantity": pd.array([], dtype="int32"),
            "unit_price": pd.array([], dtype="float64"),
            "line_amount": pd.array([], dtype="float64"),
            "placed_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
