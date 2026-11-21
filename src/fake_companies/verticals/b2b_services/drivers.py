"""B2B services latent driver catalog: known driver names + panel construction.

Lead volumes carry weekly/annual/holiday seasonality (B2B runs weekday-heavy
and dies over holidays — express that via weekly_shape / holiday_effect).
Stage conversions and the two scale drivers carry only noise; their calendar
shape emerges downstream from the lead volumes they act on.

Win rate and sales-cycle length are NOT drivers: they emerge from the
per-stage advance rates and durations (aggregate realism from raw rows).
"""

from __future__ import annotations

import numpy as np

from ...core import RngHub, growth_curve
from ...core.calendar import Calendar
from ...latent.panel import DriverPanel
from ...latent.shape import ar1_lognormal, seasonality_volume
from .config import B2BServicesScenarioConfig as ScenarioConfig


def known_drivers(cfg: ScenarioConfig) -> set[str]:
    """The set of driver names a rate anomaly may target (for validation)."""
    names = {f"leads.{src}" for src in cfg.leads.sources}
    names.update(f"stage_conversion.{s.name}" for s in cfg.pipeline.stages)
    names.add("deal_size")
    names.add("cycle_time_scale")
    return names | late_drivers(cfg)


def late_drivers(cfg: ScenarioConfig) -> set[str]:
    """Drivers added after the surprise pool was fixed, kept out of it so every
    existing config's surprises stay as they were.

    ``delivery_time_scale``: a multiplier on the days from signing to
    delivery for contracts signed that day, 1.0 unless an anomaly moves it.
    Above 1 the delivery team is behind: balance invoices, which are issued
    at delivery, arrive later while bookings do not move.
    """
    return {"delivery_time_scale"}


def surprise_drivers(cfg: ScenarioConfig) -> set[str]:
    return known_drivers(cfg) - late_drivers(cfg)


def build_drivers(cfg: ScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
    panel = DriverPanel(cal)
    season = seasonality_volume(cfg, cal)
    n = cal.n_days
    ns = cfg.noise

    def noise(name: str, sigma_scale: float = 1.0) -> np.ndarray:
        return ar1_lognormal(rng.stream(f"noise.{name}"), n, ns.day_sigma * sigma_scale, ns.ar1)

    # --- volume drivers: leads per source ------------------------------------ #
    for src, spec in cfg.leads.sources.items():
        growth = growth_curve(spec.growth, cal)
        panel.set(f"leads.{src}", spec.baseline * growth * season * noise(f"leads.{src}"))

    # --- probability drivers: advance rate out of each stage ----------------- #
    for stage in cfg.pipeline.stages:
        name = f"stage_conversion.{stage.name}"
        panel.set(name, np.clip(stage.advance_rate * noise(name, 0.5), 0.0, 0.999))

    # --- scale drivers (mean ~1 multipliers) --------------------------------- #
    panel.set("deal_size", noise("deal_size", 0.8))
    panel.set("cycle_time_scale", noise("cycle_time_scale", 0.8))
    # Exactly 1.0 unless an anomaly moves it: no noise and no stream, so every
    # draw and byte is as before.
    panel.set("delivery_time_scale", np.ones(n))

    return panel
