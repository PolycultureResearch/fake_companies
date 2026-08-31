"""``erp.shipments`` — replenishment orders lagging consumer POS demand.

Each account (direct retail banner, or a distributor's share of the
independents banner) reorders against LAST week's scan units: cases =
units / case_size with lognormal ordering noise, shipped a few days into the
week. That built-in lag is the vertical's signature causal shape — a POS lift
(promo, demand spike) reaches wholesale revenue one to two weeks later.
Off-invoice trade discounts apply while a promo is active at ship time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ..config import CPGWholesaleScenarioConfig as ScenarioConfig
from .catalog import ProductIndex
from .pos import promo_grids


def build_shipments(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    products: ProductIndex,
    accounts: pd.DataFrame,
    pos_units: np.ndarray,  # (W, B, P)
    starts: np.ndarray,
) -> pd.DataFrame:
    gen = rng.stream("cpg_shipments")
    banners = list(cfg.market.retailers)
    categories = products.categories
    n_w, _, n_p = pos_units.shape
    last_day = cal.n_days - 1
    oc = cfg.ordering

    # Demand seen by ordering in week w = POS of week w-1 (bootstrap: week 0).
    demand = np.concatenate([pos_units[:1], pos_units[:-1]], axis=0).astype(float)

    _, disc = promo_grids(cfg, cal, starts, banners, categories)  # (W, B, C)
    cat_of_p = products.product_cat_idx

    # Account -> (banner index, demand share) from the accounts frame.
    acct_banner = [banners.index(b) for b in accounts["banner"]]
    acct_share = accounts["share"].to_numpy(dtype=float)
    n_a = len(acct_banner)

    rows_w, rows_a, rows_p, rows_cases, rows_disc = [], [], [], [], []
    for ai in range(n_a):  # <= ~10 accounts, not per-entity
        b = acct_banner[ai]
        share_demand = demand[:, b, :] * acct_share[ai]  # (W, P)
        noise = np.exp(
            gen.normal(0.0, oc.order_noise_sigma, size=(n_w, n_p)) - 0.5 * oc.order_noise_sigma**2
        )
        cases = np.round(share_demand * noise / products.case_sizes[None, :]).astype(np.int64)
        w_ix, p_ix = np.nonzero(cases > 0)
        rows_w.append(w_ix)
        rows_a.append(np.full(len(w_ix), ai, dtype=np.int64))
        rows_p.append(p_ix)
        rows_cases.append(cases[w_ix, p_ix])
        rows_disc.append(disc[w_ix, b, cat_of_p[p_ix]])

    w_ix = np.concatenate(rows_w)
    a_ix = np.concatenate(rows_a)
    p_ix = np.concatenate(rows_p)
    cases = np.concatenate(rows_cases)
    disc_pct = np.concatenate(rows_disc)

    # Ship a few days into the ordering week, during business hours.
    lag_days = np.maximum(
        0,
        np.round(
            np.exp(
                gen.normal(
                    np.log(max(oc.reorder_lag_days_mean, 1e-9)) - 0.5 * oc.reorder_lag_sigma**2,
                    oc.reorder_lag_sigma,
                    size=len(w_ix),
                )
            )
        ).astype(np.int64),
    )
    ship_day = starts[w_ix] + lag_days
    in_time = ship_day <= last_day
    w_ix, a_ix, p_ix = w_ix[in_time], a_ix[in_time], p_ix[in_time]
    cases, disc_pct, ship_day = cases[in_time], disc_pct[in_time], ship_day[in_time]
    n = len(w_ix)

    secs = 8 * 3600 + gen.integers(0, 9 * 3600, size=n)  # 08:00-17:00 dock times
    shipped_at = np.datetime64(cal.start, "s") + (
        ship_day.astype("int64") * 86400 + secs.astype("int64")
    ).astype("timedelta64[s]")

    case_price = products.case_prices[p_ix]
    gross = np.round(cases * case_price, 2)
    discount = np.round(gross * disc_pct, 2)

    acct_ids = accounts["account_id"].to_numpy()
    acct_channel = accounts["channel_type"].to_numpy(dtype=object)

    df = pd.DataFrame(
        {
            "account_id": acct_ids[a_ix],
            "product_id": (p_ix + 1).astype(np.int64),
            "category": np.asarray(categories, dtype=object)[cat_of_p[p_ix]],
            "channel_type": acct_channel[a_ix],
            "cases": cases.astype(np.int32),
            "case_price": case_price,
            "gross_amount": gross,
            "discount_amount": discount,
            "net_amount": np.round(gross - discount, 2),
            "currency": cfg.company.currency,
            "shipped_at": shipped_at,
        }
    )
    df = df.sort_values(["shipped_at", "account_id", "product_id"], kind="stable").reset_index(
        drop=True
    )
    df.insert(0, "shipment_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT
    return df
