"""Retail DTC latent driver catalog: known driver names + panel construction.

Volume drivers (spend, organic/fixed sessions) carry weekly/annual/holiday
seasonality — retail is strongly holiday-peaked, which the shared calendar
seasonality expresses. Probability/intensity drivers carry only noise; their
seasonality emerges downstream from the session volumes they act on.

AOV is deliberately NOT a driver: it emerges from items_per_order x price mix
x promos (aggregate realism must come from raw rows).
"""

from __future__ import annotations

import numpy as np

from ...core import RngHub, growth_curve
from ...core.calendar import Calendar
from ...latent.panel import DriverPanel
from ...latent.shape import ar1_lognormal, seasonality_volume
from .config import RetailDTCScenarioConfig as ScenarioConfig


def known_drivers(cfg: ScenarioConfig) -> set[str]:
    """The set of driver names a rate anomaly may target (for validation)."""
    names: set[str] = set()
    for ch, spec in cfg.traffic.channels.items():
        if spec.kind == "paid":
            names.add(f"spend.{ch}")
        else:
            names.add(f"sessions.{ch}")
        names.add(f"conversion_rate.{ch}")
    names.add("items_per_order")
    names.add("repeat_rate")
    for cat in cfg.catalog.categories:
        names.add(f"category_demand.{cat}")
        names.add(f"return_rate.{cat}")
    return names


def build_drivers(cfg: ScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
    panel = DriverPanel(cal)
    season = seasonality_volume(cfg, cal)
    n = cal.n_days
    ns = cfg.noise

    def noise(name: str, sigma_scale: float = 1.0) -> np.ndarray:
        return ar1_lognormal(rng.stream(f"noise.{name}"), n, ns.day_sigma * sigma_scale, ns.ar1)

    # --- volume drivers: spend (paid) and organic/fixed sessions ------------ #
    for ch, spec in cfg.traffic.channels.items():
        if spec.kind == "paid":
            growth = growth_curve(spec.spend_growth or _flat(), cal)
            base = float(spec.spend_baseline)  # type: ignore[arg-type]
            panel.set(f"spend.{ch}", base * growth * season * noise(f"spend.{ch}"))
        else:
            growth = growth_curve(spec.growth, cal)
            base = float(spec.baseline)  # type: ignore[arg-type]
            panel.set(f"sessions.{ch}", base * growth * season * noise(f"sessions.{ch}"))

    # --- probability drivers: session -> order conversion per channel ------- #
    for ch in cfg.traffic.channels:
        base = cfg.conversion.order_rate.get(ch, cfg.conversion.order_rate_default)
        panel.set(f"conversion_rate.{ch}", _prob(base * noise(f"conversion_rate.{ch}", 0.5)))

    # --- basket / repeat intensity drivers ----------------------------------- #
    panel.set(
        "items_per_order",
        np.maximum(1.0, cfg.basket.items_per_order_mean * noise("items_per_order", 0.5)),
    )
    panel.set("repeat_rate", _prob(cfg.repeat.monthly_repeat_rate * noise("repeat_rate", 0.5)))

    # --- per-category demand (mean ~1 mix multiplier) and return rates ------- #
    # category_demand reweights which categories land in baskets AND scales
    # line intensity, so a demand drop shows in units and revenue for that
    # category while the topline conversion stays clean.
    for cat in cfg.catalog.categories:
        panel.set(f"category_demand.{cat}", noise(f"category_demand.{cat}", 1.0))
        base = cfg.returns.rate.get(cat, cfg.returns.rate_default)
        panel.set(f"return_rate.{cat}", _prob(base * noise(f"return_rate.{cat}", 0.5)))

    return panel


def _flat():
    from ...config.schema import GrowthConfig

    return GrowthConfig(kind="flat")


def _prob(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr, 0.0, 0.999)
