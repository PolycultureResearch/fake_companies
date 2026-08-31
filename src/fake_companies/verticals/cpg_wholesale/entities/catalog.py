"""Reference data: products, trading-partner accounts, and the trade calendar."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ..config import CPGWholesaleScenarioConfig as ScenarioConfig

_HERBS = [
    "chamomile",
    "ginger",
    "elderberry",
    "peppermint",
    "nettle",
    "lemon balm",
    "echinacea",
    "turmeric",
    "valerian",
    "hibiscus",
    "rooibos",
    "fennel",
    "dandelion",
    "tulsi",
    "licorice",
    "raspberry leaf",
]
_FORMS = {"teas": "Tea", "tinctures": "Tincture", "capsules": "Capsules"}


@dataclass
class ProductIndex:
    """Arrays indexed by ``product_id - 1`` for vectorized POS/shipment math."""

    categories: list[str]  # config order
    product_cat_idx: np.ndarray
    case_sizes: np.ndarray
    case_prices: np.ndarray
    msrps: np.ndarray
    weights: np.ndarray  # consumer-demand weight per product, mean ~1


def build_catalog(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub
) -> tuple[pd.DataFrame, ProductIndex]:
    gen = rng.stream("cpg_catalog")
    nominal_loaded = dt.datetime.combine(cal.end, dt.time(3, 0))

    rows: list[dict] = []
    cat_idx: list[int] = []
    weights: list[float] = []
    pid = 1
    for ci, (cat, spec) in enumerate(cfg.catalog.categories.items()):
        form = _FORMS.get(cat, cat.title())
        for _ in range(spec.n_products):
            herb = _HERBS[int(gen.integers(0, len(_HERBS)))]
            case_price = round(float(gen.uniform(spec.case_price_min, spec.case_price_max)), 2)
            msrp = round(case_price / spec.case_size * spec.retail_markup, 2)
            rows.append(
                {
                    "product_id": pid,
                    "sku": f"{cat[:3]}-{herb.replace(' ', '_')}-{pid:03d}",
                    "product_name": f"{herb.title()} {form}",
                    "category": cat,
                    "case_size": spec.case_size,
                    "case_price": case_price,
                    "msrp": msrp,
                    "currency": cfg.company.currency,
                }
            )
            cat_idx.append(ci)
            weights.append(spec.popularity)
            pid += 1

    frame = pd.DataFrame(rows)
    frame["_loaded_at"] = nominal_loaded

    w = np.asarray(weights, dtype=float)
    index = ProductIndex(
        categories=list(cfg.catalog.categories),
        product_cat_idx=np.asarray(cat_idx, dtype=np.int64),
        case_sizes=frame["case_size"].to_numpy(),
        case_prices=frame["case_price"].to_numpy(),
        msrps=frame["msrp"].to_numpy(),
        weights=w * len(w) / w.sum(),
    )
    return frame, index


def build_accounts(cfg: ScenarioConfig, cal: Calendar) -> pd.DataFrame:
    """One account per retailer banner plus the distributors (reference data)."""
    nominal_loaded = dt.datetime.combine(cal.end, dt.time(3, 0))
    rows: list[dict] = []
    aid = 1
    for banner, r in cfg.market.retailers.items():
        if banner == cfg.market.independents_banner:
            continue  # independents buy through distributors, not directly
        rows.append(
            {
                "account_id": aid,
                "name": banner.replace("_", " ").title(),
                "kind": "retailer",
                "channel_type": r.channel_type,
                "banner": banner,
                "region": "national",
                "share": 1.0,  # carried for the shipments stage (dropped on write)
            }
        )
        aid += 1
    for name, d in cfg.market.distributors.items():
        rows.append(
            {
                "account_id": aid,
                "name": name.replace("_", " ").title(),
                "kind": "distributor",
                "channel_type": "independents",
                "banner": cfg.market.independents_banner,
                "region": d.region,
                "share": d.share,
            }
        )
        aid += 1
    frame = pd.DataFrame(rows)
    frame["_loaded_at"] = nominal_loaded
    return frame


def build_trade_promotions(cfg: ScenarioConfig, cal: Calendar) -> pd.DataFrame:
    nominal_loaded = dt.datetime.combine(cal.end, dt.time(3, 0))
    rows = [
        {
            "promo_id": i + 1,
            "name": p.name,
            "retailer_banner": p.banner,
            "category": p.category,
            "start_date": p.window.start,
            "end_date": p.window.end or p.window.start,
            "discount_pct": p.discount_pct,
        }
        for i, p in enumerate(cfg.promos)
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "promo_id",
            "name",
            "retailer_banner",
            "category",
            "start_date",
            "end_date",
            "discount_pct",
        ],
    )
    frame["_loaded_at"] = nominal_loaded
    return frame
