"""Latent layer: driver panel, shape mechanisms, rate anomalies.

Driver *catalogs* (which drivers exist and how they're parameterized) are
vertical-owned — see ``fake_companies.verticals.<name>.drivers``. This package
holds the generic machinery they build on.
"""

from __future__ import annotations

from .events import apply_rate_events, build_rate_multiplier
from .panel import DriverPanel, driver_key
from .shape import ar1_lognormal, seasonality_volume

__all__ = [
    "DriverPanel",
    "apply_rate_events",
    "ar1_lognormal",
    "build_rate_multiplier",
    "driver_key",
    "seasonality_volume",
]
