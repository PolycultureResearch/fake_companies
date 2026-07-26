"""Observation layer: `_loaded_at` connector models + DQ corruptions.

M4 fills in the loading model (``loading.py``) and the data-quality corruptions
(``dq.py`` — volume_dropout, null_spike, distribution_shift, loading_delay,
duplicate_rows). The loading model stamps ``_loaded_at`` on every raw frame; the
dq layer then mutates *observed* rows over each anomaly window, emitting one
:class:`GroundTruthRecord` per dq anomaly. Business truth is never touched.
"""

from __future__ import annotations

import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..groundtruth import GroundTruthRecord
from .dq import apply_dq
from .loading import apply_loading

__all__ = ["apply_loading_and_dq"]


def apply_loading_and_dq(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    frames: dict[str, pd.DataFrame],
    resolved: list | None = None,
) -> list[GroundTruthRecord]:
    """Apply the loading model, then the dq corruptions.

    First fills ``_loaded_at`` on every raw frame present in ``frames``; then
    applies each dq anomaly in ``resolved`` and returns the resulting ground
    truth (one record per dq anomaly). ``resolved`` defaults to ``None`` so the
    loading model still runs before the anomaly set has been wired in.
    """
    apply_loading(cfg, cal, rng, frames)
    if not resolved:
        return []
    return apply_dq(cfg, cal, rng, frames, resolved)
