"""CPG wholesale entity layer: raw rows drawn stochastically from the latent panel.

``build_all`` runs the chain in dependency order:

    catalog/accounts/promos (reference data) -> ad_spend (decoupled)
        -> weekly POS scan sales -> replenishment shipments (lagging POS)
"""

from __future__ import annotations

import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ..config import CPGWholesaleScenarioConfig as ScenarioConfig

__all__ = ["build_all"]


def build_all(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> None:
    from .catalog import build_accounts, build_catalog, build_trade_promotions
    from .marketing import build_ad_spend
    from .pos import build_scan_sales
    from .shipments import build_shipments

    products_frame, products = build_catalog(cfg, cal, rng)
    accounts = build_accounts(cfg, cal)
    frames["erp.accounts"] = accounts
    frames["erp.products"] = products_frame
    frames["promo.trade_promotions"] = build_trade_promotions(cfg, cal)

    frames["ad_platform.ad_spend"] = build_ad_spend(cfg, cal, rng, panel)

    scan, pos_units, starts = build_scan_sales(cfg, cal, rng, panel, products)
    frames["pos.scan_sales"] = scan
    frames["erp.shipments"] = build_shipments(cfg, cal, rng, products, accounts, pos_units, starts)
