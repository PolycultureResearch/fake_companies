"""Entity layer: raw rows drawn stochastically from the latent panel.

M0 ships a no-op ``build_all``; M2-M4 add marketing/traffic/funnel, lifecycle/
billing, and usage generators. Each writes its frames into the shared ``frames``
dict keyed by table fqn (e.g. ``"web.sessions"``).
"""

from __future__ import annotations

import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel

__all__ = ["build_all"]


def build_all(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    frames: dict[str, pd.DataFrame],
) -> None:
    """Stub: builds no entities yet. Populated across M2-M4."""
    return
