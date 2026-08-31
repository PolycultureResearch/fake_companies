"""CPG wholesale latent driver catalog: known driver names + panel construction.

The defining property of this vertical: ``spend.<channel>`` exists (the brand
runs ads) but nothing downstream reads it — marketing is causally decoupled
from sales, unlike every web vertical. Consumer demand carries the calendar
seasonality (herbal tea peaks in winter via annual_amp/peak_doy); per-banner
velocity is a mean-1 multiplier for shelf-placement effects.
"""

from __future__ import annotations

import numpy as np

from ...core import RngHub, growth_curve
from ...core.calendar import Calendar
from ...latent.panel import DriverPanel
from ...latent.shape import ar1_lognormal, seasonality_volume
from .config import CPGWholesaleScenarioConfig as ScenarioConfig


def known_drivers(cfg: ScenarioConfig) -> set[str]:
    """The set of driver names a rate anomaly may target (for validation)."""
    names = {f"spend.{ch}" for ch in cfg.marketing.channels}
    names.update(f"consumer_demand.{cat}" for cat in cfg.catalog.categories)
    names.update(f"velocity.{banner}" for banner in cfg.market.retailers)
    return names


def build_drivers(cfg: ScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
    panel = DriverPanel(cal)
    season = seasonality_volume(cfg, cal)
    n = cal.n_days
    ns = cfg.noise

    def noise(name: str, sigma_scale: float = 1.0) -> np.ndarray:
        return ar1_lognormal(rng.stream(f"noise.{name}"), n, ns.day_sigma * sigma_scale, ns.ar1)

    # --- brand marketing spend (decoupled from sales) ------------------------ #
    for ch, spec in cfg.marketing.channels.items():
        growth = growth_curve(spec.growth, cal)
        panel.set(f"spend.{ch}", spec.spend_baseline * growth * season * noise(f"spend.{ch}"))

    # --- consumer demand per category (carries the calendar seasonality) ----- #
    for cat in cfg.catalog.categories:
        panel.set(f"consumer_demand.{cat}", season * noise(f"consumer_demand.{cat}"))

    # --- per-banner shelf velocity (mean ~1 multiplier) ---------------------- #
    for banner in cfg.market.retailers:
        panel.set(f"velocity.{banner}", noise(f"velocity.{banner}", 0.7))

    return panel
