"""Latent layer: driver panel, growth/seasonality build, rate anomalies.

M0 ships no-op stubs so the pipeline runs end-to-end; M1 replaces
``build_drivers`` and ``apply_rate_events`` with the real implementations.
"""

from __future__ import annotations

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..groundtruth import GroundTruthRecord
from .panel import DriverPanel, driver_key

__all__ = ["DriverPanel", "apply_rate_events", "build_drivers", "driver_key"]


def build_drivers(cfg: ScenarioConfig, cal: Calendar, rng: RngHub) -> DriverPanel:
    """Stub: empty panel. Replaced in M1."""
    return DriverPanel(cal)


def apply_rate_events(
    panel: DriverPanel,
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
) -> tuple[DriverPanel, list[GroundTruthRecord]]:
    """Stub: no rate anomalies. Replaced in M1."""
    return panel, []
