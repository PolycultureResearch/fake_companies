"""Deterministic growth curves.

Every curve returns a multiplicative shape normalized to 1.0 at the timeline
start, so a driver is ``baseline * growth(t) * ...``. Growth is baseline shape,
never an anomaly — it does not enter ground truth.
"""

from __future__ import annotations

import numpy as np

from ..config.schema import GrowthConfig
from .calendar import Calendar


def growth_curve(cfg: GrowthConfig, cal: Calendar) -> np.ndarray:
    """Evaluate a :class:`GrowthConfig` over the calendar grid."""
    t = cal.day_index
    if cfg.kind == "flat":
        return np.ones_like(t)
    if cfg.kind == "linear":
        return np.maximum(0.0, 1.0 + cfg.rate * t)
    if cfg.kind == "exponential":
        return np.power(1.0 + cfg.rate, t)
    if cfg.kind == "logistic":
        return _logistic(cfg, cal)
    if cfg.kind == "piecewise":
        return _piecewise(cfg, cal)
    raise ValueError(f"unknown growth kind {cfg.kind!r}")


def _logistic(cfg: GrowthConfig, cal: Calendar) -> np.ndarray:
    # Grows from ~1.0 toward `capacity`. midpoint defaults to the series middle.
    cap = float(cfg.capacity)  # type: ignore[arg-type]
    t = cal.day_index
    if cfg.midpoint is not None:
        t_mid = float(cal.date_to_index(cfg.midpoint))
    else:
        t_mid = t[-1] / 2.0
    k = cfg.steepness if cfg.steepness is not None else 4.0 / max(t[-1], 1.0)
    # Standard logistic between the start level (~1) and the capacity multiplier.
    raw = 1.0 / (1.0 + np.exp(-k * (t - t_mid)))
    raw0 = 1.0 / (1.0 + np.exp(-k * (0.0 - t_mid)))
    # Anchor so growth(0) == 1.0 and asymptote == cap.
    return 1.0 + (cap - 1.0) * (raw - raw0) / (1.0 - raw0)


def _piecewise(cfg: GrowthConfig, cal: Calendar) -> np.ndarray:
    assert cfg.segments is not None
    segs = sorted(cfg.segments, key=lambda s: s.start)
    out = np.ones(cal.n_days, dtype=float)
    level = 1.0  # multiplicative level carried across segment boundaries
    # Seed the leading region (before the first segment) as flat at 1.0.
    for j, seg in enumerate(segs):
        i0 = cal.date_to_index(seg.start)
        if j == 0:
            out[:i0] = level
        # Determine end of this segment.
        i1 = cal.date_to_index(segs[j + 1].start) if j + 1 < len(segs) else cal.n_days
        local_t = np.arange(i1 - i0, dtype=float)
        if seg.kind == "flat":
            shape = np.ones_like(local_t)
        elif seg.kind == "linear":
            shape = np.maximum(0.0, 1.0 + seg.rate * local_t)
        elif seg.kind == "exponential":
            shape = np.power(1.0 + seg.rate, local_t)
        else:  # pragma: no cover - schema restricts kinds
            raise ValueError(f"unknown piecewise segment kind {seg.kind!r}")
        out[i0:i1] = level * shape
        level = float(out[i1 - 1])  # continuity into the next segment
    return out
