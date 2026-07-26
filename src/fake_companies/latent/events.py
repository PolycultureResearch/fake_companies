"""Apply rate anomalies to the latent panel.

A rate anomaly multiplies its target driver's daily rate over a window. Because
downstream entities draw from the (now-modified) driver, effects cascade causally
(spend cut -> fewer paid sessions -> fewer signups -> less MRR). Segmented events
write a segment-specific driver, leaving the topline clean so the anomaly is
invisible until you slice by that dimension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ..config import ScenarioConfig
from ..config.schema import Window
from ..core.calendar import Calendar
from ..groundtruth import GroundTruthRecord
from .build import known_drivers
from .panel import DriverPanel

if TYPE_CHECKING:
    from ..anomalies import ResolvedAnomaly


def apply_rate_events(
    panel: DriverPanel,
    resolved: list[ResolvedAnomaly],
    cfg: ScenarioConfig,
    cal: Calendar,
) -> tuple[DriverPanel, list[GroundTruthRecord]]:
    # Imported lazily to avoid a latent<->anomalies import cycle.
    from ..anomalies import affected_metrics_for_driver, rate_anomalies

    valid = known_drivers(cfg)
    records: list[GroundTruthRecord] = []
    for r in rate_anomalies(resolved):
        a = r.spec
        if a.target not in valid:
            raise ValueError(
                f"rate anomaly {a.name!r} targets unknown driver {a.target!r}; "
                f"valid drivers: {sorted(valid)}"
            )
        mult = build_rate_multiplier(a.type, cal, a.window, a.magnitude, a.params)
        base = panel.get(a.target)  # topline (segment falls back to topline)
        panel.set(a.target, base * mult, segment=a.segment)
        records.append(
            GroundTruthRecord(
                id=a.name,
                kind="rate",
                type=a.type,
                origin=r.origin,
                target=a.target,
                segment=a.segment,
                start=a.window.start,
                end=a.window.end,
                magnitude=a.magnitude,
                affected_metrics=affected_metrics_for_driver(a.target),
                params=a.params or {},
            )
        )
    return panel, records


def build_rate_multiplier(
    atype: str,
    cal: Calendar,
    window: Window,
    magnitude: float,
    params: dict | None,
) -> np.ndarray:
    """Length-``n_days`` multiplicative factor (1.0 outside the event)."""
    n = cal.n_days
    mult = np.ones(n)
    i0 = cal.date_to_index(window.start)
    i1 = cal.date_to_index(window.end) if window.end is not None else n - 1

    if atype in ("spike", "drop"):
        mult[i0 : i1 + 1] = magnitude
    elif atype == "level_shift":
        mult[i0 : i1 + 1] = magnitude  # recovers to 1.0 after i1 (if end given)
    elif atype == "ramp":
        end = i1 if window.end is not None else min(n - 1, i0 + 14)
        span = max(1, end - i0)
        ramp = 1.0 + (magnitude - 1.0) * (np.arange(end - i0 + 1) / span)
        mult[i0 : end + 1] = ramp
        mult[end + 1 :] = magnitude  # hold the new level
    elif atype == "trend_change":
        # Persistent compounding slope break from i0 onward.
        k = np.arange(n - i0, dtype=float)
        mult[i0:] = np.power(magnitude, k)
    elif atype == "seasonality_change":
        dow_mult = _seasonality_change_dow(magnitude, params)
        seg = slice(i0, i1 + 1)
        mult[seg] = dow_mult[cal.dow[seg]]
    else:  # pragma: no cover - schema restricts types
        raise ValueError(f"unknown rate anomaly type {atype!r}")
    return mult


def _seasonality_change_dow(magnitude: float, params: dict | None) -> np.ndarray:
    """Per-DOW multiplier for a seasonality_change event.

    ``params.dow_multipliers`` (length 7, Mon..Sun) overrides directly; otherwise
    ``magnitude`` scales the weekend (Sat/Sun) relative to weekdays.
    """
    p = params or {}
    dm = p.get("dow_multipliers")
    if dm is not None:
        arr = np.asarray(dm, dtype=float)
        if arr.shape != (7,):
            raise ValueError("seasonality_change dow_multipliers must have length 7")
        return arr
    out = np.ones(7)
    out[5] = magnitude
    out[6] = magnitude
    return out
