"""Retail DTC entity layer: raw rows drawn stochastically from the latent panel.

``build_all`` runs the chain in dependency order, writing each frame into the
shared ``frames`` dict keyed by table fqn:

    catalog -> ad_spend -> sessions -> first orders + customers
            -> repeat orders (monthly hazard) -> baskets (lines + amounts)
            -> shipments -> returns (delayed) -> payment transactions
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ..config import RetailDTCScenarioConfig as ScenarioConfig

__all__ = ["build_all"]


def build_all(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> None:
    from ....shared.marketing import build_ad_spend
    from ....shared.traffic import build_sessions
    from .basket import build_baskets
    from .catalog import build_catalog
    from .fulfillment import build_shipments
    from .orders import build_first_orders
    from .payments import build_transactions
    from .repeat import build_repeat_orders
    from .returns import build_returns

    products, catalog = build_catalog(cfg, cal, rng)
    frames["shop_db.products"] = products
    frames["ad_platform.ad_spend"] = build_ad_spend(cfg, cal, rng, panel)

    sessions = build_sessions(cfg, cal, rng, panel)
    first, customers = build_first_orders(cfg, cal, rng, panel, sessions)
    frames["web.sessions"] = sessions
    frames["shop_db.customers"] = customers

    repeat = build_repeat_orders(cfg, cal, rng, panel, first)

    # Merge first + repeat orders into one id-assigned skeleton by event time.
    skeleton = pd.concat([first, repeat], ignore_index=True)
    skeleton = skeleton.sort_values("placed_at", kind="stable").reset_index(drop=True)
    skeleton.insert(0, "order_id", np.arange(1, len(skeleton) + 1, dtype=np.int64))

    orders, items = build_baskets(cfg, cal, rng, panel, skeleton, catalog)
    frames["shop_db.orders"] = orders
    frames["shop_db.order_items"] = items

    shipments = build_shipments(cfg, cal, rng, orders)
    frames["fulfillment.shipments"] = shipments

    returns = build_returns(cfg, cal, rng, panel, orders, items, shipments, catalog)
    frames["shop_db.returns"] = returns

    frames["payments.transactions"] = build_transactions(cfg, cal, rng, orders, returns)
