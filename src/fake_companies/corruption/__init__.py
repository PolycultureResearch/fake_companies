"""Observation layer: `_loaded_at` connector models + DQ corruptions.

M0 ships a no-op; M4 adds the loading model and the data-quality corruptions
(volume_dropout, null_spike, distribution_shift, loading_delay, duplicate_rows),
each emitting a :class:`GroundTruthRecord`.
"""

from __future__ import annotations

import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..groundtruth import GroundTruthRecord

__all__ = ["apply_loading_and_dq"]


def apply_loading_and_dq(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    frames: dict[str, pd.DataFrame],
    resolved: list,
) -> list[GroundTruthRecord]:
    """Stub: no loading model or corruption yet. Populated in M4."""
    return []
