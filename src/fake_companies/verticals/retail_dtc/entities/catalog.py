"""SKU catalog: the ``shop_db.products`` frame + a vectorized sampling index.

SKUs are generated deterministically from the catalog config: each category
gets ``n_skus`` style/color/size combinations with retail-shaped prices
(x.95 endings) and a unit cost derived from the configured cost ratio.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ..config import RetailDTCScenarioConfig as ScenarioConfig

# Style-name pool for product names (outdoor/lifestyle flavored, brand-neutral).
_STYLES = [
    "ridge",
    "cascade",
    "summit",
    "meadow",
    "granite",
    "juniper",
    "basin",
    "timber",
    "cirrus",
    "vista",
    "canyon",
    "harbor",
    "prairie",
    "sierra",
    "tundra",
    "willow",
]


@dataclass
class SkuCatalog:
    """Arrays indexed by ``sku_id - 1`` plus per-category slices for sampling."""

    categories: list[str]  # config order
    sku_category_idx: np.ndarray  # per-sku category index
    prices: np.ndarray
    unit_costs: np.ndarray
    popularity: np.ndarray  # per-category base weight, config order
    cat_offsets: np.ndarray  # first sku index per category
    cat_sizes: np.ndarray  # sku count per category

    def sku_for_category(self, cat_idx: np.ndarray, u: np.ndarray) -> np.ndarray:
        """Uniform SKU draw within each line's category from uniforms ``u``."""
        return self.cat_offsets[cat_idx] + np.minimum(
            (u * self.cat_sizes[cat_idx]).astype(np.int64), self.cat_sizes[cat_idx] - 1
        )


def build_catalog(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub
) -> tuple[pd.DataFrame, SkuCatalog]:
    gen = rng.stream("catalog")
    rows: list[dict] = []
    sku_id = 1
    cat_offsets: list[int] = []
    cat_sizes: list[int] = []

    for cat, spec in cfg.catalog.categories.items():
        cat_offsets.append(sku_id - 1)
        cat_sizes.append(spec.n_skus)
        singular = cat.removesuffix("s")
        for _ in range(spec.n_skus):
            style = _STYLES[int(gen.integers(0, len(_STYLES)))]
            color = spec.colors[int(gen.integers(0, len(spec.colors)))]
            size = spec.sizes[int(gen.integers(0, len(spec.sizes)))]
            # Retail-shaped price: uniform in range, snapped to a .95 ending.
            raw = float(gen.uniform(spec.price_min, spec.price_max))
            price = float(np.floor(raw)) + 0.95
            unit_cost = round(price * spec.unit_cost_ratio * float(gen.uniform(0.92, 1.08)), 2)
            rows.append(
                {
                    "sku_id": sku_id,
                    "sku": f"{singular}-{style}-{color}-{size}-{sku_id:04d}",
                    "product_name": f"{style.title()} {singular.title()}",
                    "category": cat,
                    "color": color,
                    "size": size,
                    "price": price,
                    "unit_cost": unit_cost,
                    "currency": cfg.company.currency,
                }
            )
            sku_id += 1

    frame = pd.DataFrame(rows)
    nominal_loaded = dt.datetime.combine(cal.end, dt.time(3, 0))
    frame["_loaded_at"] = nominal_loaded

    catalog = SkuCatalog(
        categories=list(cfg.catalog.categories),
        sku_category_idx=np.repeat(np.arange(len(cat_sizes)), cat_sizes),
        prices=frame["price"].to_numpy(),
        unit_costs=frame["unit_cost"].to_numpy(),
        popularity=np.array(
            [spec.popularity for spec in cfg.catalog.categories.values()], dtype=float
        ),
        cat_offsets=np.asarray(cat_offsets, dtype=np.int64),
        cat_sizes=np.asarray(cat_sizes, dtype=np.int64),
    )
    return frame, catalog
