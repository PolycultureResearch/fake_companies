"""Build the latent driver panel from a scenario config.

Each driver is a length-``n_days`` array of daily rates:

    rate = baseline * growth(t) * seasonality(t) * AR(1)-lognormal-noise(t)

Volume drivers (spend, organic/fixed sessions) carry weekly/annual/holiday
seasonality; probability/intensity drivers (signup_rate, churn, ...) carry only
noise (their seasonality emerges downstream from the volumes they act on).

Paid-channel *sessions* are intentionally NOT drivers: the traffic entity derives
them from the (possibly anomalized) ``spend.<channel>`` driver / cpc, so a spend
cut cascades causally into sessions. See ``docs/plan.md``.
"""

from __future__ import annotations

import numpy as np

from ..config import ScenarioConfig
from ..core import RngHub, growth_curve
from ..core.calendar import Calendar
from .panel import DriverPanel


def known_drivers(cfg: ScenarioConfig) -> set[str]:
    """The set of driver names a rate anomaly may target (for validation)."""
    names: set[str] = set()
    for ch, spec in cfg.traffic.channels.items():
        if spec.kind == "paid":
            names.add(f"spend.{ch}")
        else:
            names.add(f"sessions.{ch}")
        names.add(f"signup_rate.{ch}")
    names.add("trial_start_rate")
    names.add("trial_convert")
    for plan in cfg.lifecycle.monthly_churn:
        names.add(f"churn.{plan}")
    names.update({"upgrade", "downgrade", "resurrect"})
    for plan in cfg.engagement.dau_over_active:
        names.add(f"dau_over_active.{plan}")
    for plan in cfg.engagement.events_per_active_day:
        names.add(f"events_per_active_day.{plan}")
    return names


def seasonality_volume(cfg: ScenarioConfig, cal: Calendar) -> np.ndarray:
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

    # --- probability drivers: signup_rate per channel ----------------------- #
    for ch in cfg.traffic.channels:
        base = cfg.funnel.signup_rate.get(ch, cfg.funnel.signup_rate_default)
        rate = np.clip(base * noise(f"signup_rate.{ch}", 0.5), 0.0, 0.99)
        panel.set(f"signup_rate.{ch}", rate)

    # --- funnel / lifecycle scalar-rate drivers ----------------------------- #
    panel.set(
        "trial_start_rate", _prob(cfg.funnel.trial_start_rate * noise("trial_start_rate", 0.5))
    )
    panel.set("trial_convert", _prob(cfg.lifecycle.trial_convert * noise("trial_convert", 0.5)))
    for plan, churn in cfg.lifecycle.monthly_churn.items():
        panel.set(f"churn.{plan}", _prob(churn * noise(f"churn.{plan}", 0.5)))
    panel.set("upgrade", _prob(cfg.lifecycle.monthly_upgrade * noise("upgrade", 0.5)))
    panel.set("downgrade", _prob(cfg.lifecycle.monthly_downgrade * noise("downgrade", 0.5)))
    panel.set("resurrect", _prob(cfg.lifecycle.monthly_resurrect * noise("resurrect", 0.5)))

    # --- engagement drivers ------------------------------------------------- #
    for plan, p in cfg.engagement.dau_over_active.items():
        panel.set(f"dau_over_active.{plan}", _prob(p * noise(f"dau_over_active.{plan}", 0.5)))
    for plan, lam in cfg.engagement.events_per_active_day.items():
        panel.set(
            f"events_per_active_day.{plan}",
            np.maximum(0.0, lam * noise(f"events_per_active_day.{plan}", 0.5)),
        )

    return panel


def _flat():
    from ..config.schema import GrowthConfig

    return GrowthConfig(kind="flat")


def _prob(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr, 0.0, 0.999)
