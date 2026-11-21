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
    return names | late_drivers(cfg)


def late_drivers(cfg: ScenarioConfig) -> set[str]:
    """Drivers added after the surprise pool was fixed, kept out of it so every
    existing config's surprises stay as they were:

    - ``price.<category>``: a list-price index (case price and shelf price
      both), 1.0 unless an anomaly moves it; POS answers it through the
      category's price elasticity.
    - ``ordering.<channel_type>``: how much of last week's sell-through the
      accounts of a channel reorder, 1.0 unless an anomaly moves it. Below 1
      a retailer is running its stock down (a destock); above 1, building it.
    """
    names = {f"price.{cat}" for cat in cfg.catalog.categories}
    names.update(f"ordering.{r.channel_type}" for r in cfg.market.retailers.values())
    return names


def surprise_drivers(cfg: ScenarioConfig) -> set[str]:
    return known_drivers(cfg) - late_drivers(cfg)


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

    # --- list prices and reorder behaviour: exactly 1.0 unless an anomaly
    # moves them. No noise and no stream, so every draw and byte is as before. #
    for name in sorted(late_drivers(cfg)):
        panel.set(name, np.ones(n))

    return panel
