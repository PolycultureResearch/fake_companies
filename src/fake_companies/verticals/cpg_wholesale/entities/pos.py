"""``pos.scan_sales`` — weekly retailer scan data per (banner, product).

Weekly units are Poisson around stores x banner velocity x product weight x
category consumer demand x active-promo lift. Consumer demand supports
per-banner segment overrides (a segmented anomaly like a regional delisting
shows only at that banner). Dollars reflect the shelf price, cut by the promo
discount while a promo runs — trade spend passes through to the shopper.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ....latent.panel import driver_key
from ..config import CPGWholesaleScenarioConfig as ScenarioConfig
from .catalog import ProductIndex


def week_starts(cal: Calendar) -> np.ndarray:
    """Day indices of Mondays whose full week fits inside the timeline."""
    first_monday = (7 - cal.start.weekday()) % 7
    starts = np.arange(first_monday, cal.n_days - 6, 7, dtype=np.int64)
    return starts


def weekly_mean(arr: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Mean of a daily driver over each week."""
    return np.stack([arr[s : s + 7].mean() for s in starts])


def promo_grids(
    cfg: ScenarioConfig,
    cal: Calendar,
    starts: np.ndarray,
    banners: list[str],
    categories: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """(lift, discount) arrays of shape (weeks, banners, categories).

    A promo applies to a week when the week overlaps its window.
    """
    n_w, n_b, n_c = len(starts), len(banners), len(categories)
    lift = np.ones((n_w, n_b, n_c))
    disc = np.zeros((n_w, n_b, n_c))
    b_idx = {b: i for i, b in enumerate(banners)}
    c_idx = {c: i for i, c in enumerate(categories)}
    for p in cfg.promos:
        d0 = (p.window.start - cal.start).days
        d1 = (p.window.end - cal.start).days if p.window.end else d0
        overlaps = (starts + 6 >= d0) & (starts <= d1)
        lift[overlaps, b_idx[p.banner], c_idx[p.category]] = p.lift
        disc[overlaps, b_idx[p.banner], c_idx[p.category]] = p.discount_pct
    return lift, disc


def demand_grid(
    cfg: ScenarioConfig,
    panel: DriverPanel,
    starts: np.ndarray,
    banners: list[str],
    categories: list[str],
) -> np.ndarray:
    """Weekly consumer-demand multiplier (weeks, banners, categories).

    Topline per category, with ``consumer_demand.<cat>|banner=<b>`` overrides.
    """
    n_w, n_b = len(starts), len(banners)
    grid = np.empty((n_w, n_b, len(categories)))
    for ci, cat in enumerate(categories):
        name = f"consumer_demand.{cat}"
        top = weekly_mean(panel.get(name), starts)
        grid[:, :, ci] = top[:, None]
        for key in panel.rates:
            if "|" not in key or not key.startswith(f"{name}|"):
                continue
            _, seg_str = key.split("|", 1)
            segment = dict(kv.split("=", 1) for kv in seg_str.split(","))
            banner = segment.get("banner")
            if banner in banners and len(segment) == 1:
                seg_weekly = weekly_mean(panel.rates[driver_key(name, segment)], starts)
                grid[:, banners.index(banner), ci] = seg_weekly
    return grid


def build_scan_sales(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    products: ProductIndex,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Returns (scan_sales frame, weekly units grid (W,B,P), week day-starts)."""
    gen = rng.stream("cpg_pos")
    starts = week_starts(cal)
    banners = list(cfg.market.retailers)
    categories = products.categories

    stores = np.array([cfg.market.retailers[b].stores for b in banners], dtype=float)
    base_vel = np.array([cfg.market.retailers[b].velocity for b in banners], dtype=float)
    vel = np.stack(
        [weekly_mean(panel.get(f"velocity.{b}"), starts) for b in banners], axis=1
    )  # (W, B)

    demand = demand_grid(cfg, panel, starts, banners, categories)  # (W, B, C)
    lift, disc = promo_grids(cfg, cal, starts, banners, categories)  # (W, B, C)

    cat_of_p = products.product_cat_idx
    mean = (
        (stores * base_vel)[None, :, None]
        * vel[:, :, None]
        * products.weights[None, None, :]
        * demand[:, :, cat_of_p]
        * lift[:, :, cat_of_p]
    )
    units = gen.poisson(mean)  # (W, B, P)

    shelf_price = products.msrps[None, None, :] * (1.0 - disc[:, :, cat_of_p])
    dollars = np.round(units * shelf_price, 2)

    w_ix, b_ix, p_ix = np.nonzero(units)
    week_start_dates = np.array(
        [cal.start + dt.timedelta(days=int(s)) for s in starts], dtype=object
    )
    frame = pd.DataFrame(
        {
            "week_start": week_start_dates[w_ix],
            "retailer_banner": np.asarray(banners, dtype=object)[b_ix],
            "channel_type": np.asarray(
                [cfg.market.retailers[b].channel_type for b in banners], dtype=object
            )[b_ix],
            "product_id": (p_ix + 1).astype(np.int64),
            "category": np.asarray(categories, dtype=object)[cat_of_p[p_ix]],
            "units": units[w_ix, b_ix, p_ix].astype(np.int64),
            "dollars": dollars[w_ix, b_ix, p_ix],
            "currency": cfg.company.currency,
        }
    )
    frame.insert(0, "scan_id", np.arange(1, len(frame) + 1, dtype=np.int64))
    frame["_loaded_at"] = pd.NaT
    return frame, units, starts
