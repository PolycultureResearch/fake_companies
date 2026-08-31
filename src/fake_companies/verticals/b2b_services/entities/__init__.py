"""B2B services entity layer: raw rows drawn stochastically from the latent panel.

``build_all`` runs the chain in dependency order:

    leads -> accounts/contacts/deals -> stage machine + stage events
          -> contracts -> invoices (upfront/balance) -> payments
"""

from __future__ import annotations

import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ..config import B2BServicesScenarioConfig as ScenarioConfig

__all__ = ["build_all"]


def build_all(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> None:
    from .billing import build_billing
    from .pipeline import build_pipeline

    deals = build_pipeline(cfg, cal, rng, panel, frames)
    build_billing(cfg, cal, rng, deals, frames)
