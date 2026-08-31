"""Generic driver-shape mechanisms: seasonality and AR(1) lognormal noise.

Vertical driver catalogs compose these into their daily rate panels:

    rate = baseline * growth(t) * seasonality(t) * AR(1)-lognormal-noise(t)
"""

from __future__ import annotations

import numpy as np

from ..config.schema import BaseScenarioConfig
from ..core.calendar import Calendar


def seasonality_volume(cfg: BaseScenarioConfig, cal: Calendar) -> np.ndarray:
    """Weekly x annual x holiday multiplier for volume drivers (mean ~1)."""
    weekly = np.asarray(cfg.weekly_shape, dtype=float)
    weekly = weekly / weekly.mean()
    weekly_mult = weekly[cal.dow]

    peak = cfg.calendar.annual_peak_doy
    annual_mult = 1.0 + cfg.calendar.annual_amp * np.cos(2 * np.pi * (cal.doy - peak) / 365.25)

    holiday_mult = np.where(
        cal.holiday_mask(cfg.calendar.holiday_country), cfg.calendar.holiday_effect, 1.0
    )
    return weekly_mult * annual_mult * holiday_mult


def ar1_lognormal(gen: np.random.Generator, n: int, sigma: float, ar1: float) -> np.ndarray:
    """AR(1) lognormal multiplicative noise with mean ~1.0.

    The log-process is a stationary AR(1) with marginal variance ``sigma**2``;
    exponentiating and de-biasing by ``exp(-sigma**2/2)`` keeps the mean at 1.
    """
    if sigma <= 0:
        return np.ones(n)
    ar1 = float(np.clip(ar1, -0.999, 0.999))
    innov_sd = sigma * np.sqrt(1.0 - ar1**2)
    e = np.empty(n)
    e[0] = gen.normal(0.0, sigma)  # stationary initial draw
    innov = gen.normal(0.0, innov_sd, size=n)
    for t in range(1, n):  # per-day recurrence (<=730 iters/driver; not per-entity)
        e[t] = ar1 * e[t - 1] + innov[t]
    return np.exp(e - 0.5 * sigma**2)
