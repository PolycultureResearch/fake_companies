"""Resolve the full anomaly set (scripted + surprise-sampled) for a run.

Sampling happens once, deterministically, from a single ``anomalies.surprise``
RNG stream so the rate layer and the dq layer see a stable split by kind. What
can be targeted — drivers, dq tables, dq columns — comes from the vertical;
this module owns only the generic sampling mechanics and the dq-signal map.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .config import BaseScenarioConfig
from .config.schema import ScriptedAnomaly, Window
from .core import RngHub
from .core.calendar import Calendar
from .verticals.base import Vertical

_RATE_SURPRISE_TYPES = ["spike", "drop", "level_shift", "trend_change", "ramp"]
_DQ_SURPRISE_TYPES = [
    "volume_dropout",
    "null_spike",
    "distribution_shift",
    "loading_delay",
    "duplicate_rows",
]


@dataclass
class ResolvedAnomaly:
    spec: ScriptedAnomaly
    origin: str  # "scripted" | "surprise"


def resolve_anomalies(
    cfg: BaseScenarioConfig, cal: Calendar, rng: RngHub, vertical: Vertical
) -> list[ResolvedAnomaly]:
    resolved = [ResolvedAnomaly(a, "scripted") for a in cfg.anomalies.scripted]
    if cfg.anomalies.surprise is not None:
        resolved.extend(_sample_surprise(cfg, cal, rng, resolved, vertical))
    return resolved


def rate_anomalies(resolved: list[ResolvedAnomaly]) -> list[ResolvedAnomaly]:
    return [r for r in resolved if r.spec.kind == "rate"]


def dq_anomalies(resolved: list[ResolvedAnomaly]) -> list[ResolvedAnomaly]:
    return [r for r in resolved if r.spec.kind == "dq"]


# --------------------------------------------------------------------------- #
# Surprise sampling
# --------------------------------------------------------------------------- #
def _sample_surprise(
    cfg: BaseScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    existing: list[ResolvedAnomaly],
    vertical: Vertical,
) -> list[ResolvedAnomaly]:
    sc = cfg.anomalies.surprise
    assert sc is not None
    gen = rng.stream("anomalies.surprise")
    drivers = sorted(vertical.known_drivers(cfg))
    dq_tables = vertical.dq_surprise_tables()
    dq_meta = vertical.dq_targets()
    taken = [cal.date_to_index(r.spec.window.start) for r in existing]

    excluded = np.zeros(cal.n_days, dtype=bool)
    for w in sc.exclude_windows:
        excluded[cal.window_slice(w.start, w.end)] = True
    excluded[:14] = True  # warm-up margin
    excluded[-7:] = True  # trailing margin

    out: list[ResolvedAnomaly] = []
    attempts = 0
    while len(out) < sc.count and attempts < sc.count * 50:
        attempts += 1
        kind = str(gen.choice(sc.kinds))
        allowed_types = sc.types or (_RATE_SURPRISE_TYPES if kind == "rate" else _DQ_SURPRISE_TYPES)
        pool = [
            t
            for t in allowed_types
            if t in (_RATE_SURPRISE_TYPES if kind == "rate" else _DQ_SURPRISE_TYPES)
        ]
        if not pool:
            continue
        atype = str(gen.choice(pool))
        i0 = int(gen.integers(0, cal.n_days))
        if excluded[i0] or any(abs(i0 - t) < sc.min_gap_days for t in taken):
            continue
        start = cal.start + dt.timedelta(days=i0)
        magnitude = float(gen.uniform(sc.magnitude.min, sc.magnitude.max))
        spec = _build_surprise_spec(
            kind, atype, start, magnitude, cal, gen, drivers, dq_tables, dq_meta
        )
        if spec is None:
            continue
        taken.append(i0)
        out.append(ResolvedAnomaly(spec, "surprise"))
    return out


def _build_surprise_spec(
    kind: str,
    atype: str,
    start: dt.date,
    magnitude: float,
    cal: Calendar,
    gen: np.random.Generator,
    drivers: list[str],
    dq_tables: list[str],
    dq_meta: dict[str, dict[str, list[str]]],
) -> ScriptedAnomaly | None:
    dur = int(gen.integers(1, 4))
    end: dt.date | None = start + dt.timedelta(days=dur - 1)
    params: dict = {}

    if kind == "rate":
        target = str(gen.choice(drivers))
        if atype in ("spike", "drop"):
            end = start + dt.timedelta(days=int(gen.integers(0, 3)))
        elif atype in ("level_shift", "ramp"):
            end = start + dt.timedelta(days=int(gen.integers(7, 31)))
        elif atype == "trend_change":
            end = None  # persistent slope break
    else:
        target = str(gen.choice(dq_tables))
        meta = dq_meta.get(target, {})
        if atype == "null_spike":
            cols = meta.get("nullable") or []
            if not cols:
                return None
            params["column"] = str(gen.choice(cols))
        elif atype == "distribution_shift":
            cols = meta.get("categorical") or []
            if not cols:
                return None
            params["column"] = str(gen.choice(cols))
            params["skew"] = float(gen.uniform(0.4, 0.8))  # concentrate onto one level

    # Clamp window to the timeline.
    if end is not None and end > cal.end:
        end = cal.end
    return ScriptedAnomaly(
        name=f"surprise_{kind}_{atype}_{start.isoformat()}",
        kind=kind,  # type: ignore[arg-type]
        type=atype,
        target=target,
        window=Window(start=start, end=end),
        magnitude=magnitude,
        params=params or None,
    )


# --------------------------------------------------------------------------- #
# Affected dataflow signals for dq events (type-determined, vertical-agnostic)
# --------------------------------------------------------------------------- #
def affected_signals_for_dq(atype: str, params: dict | None) -> list[str]:
    col = (params or {}).get("column")
    if atype == "volume_dropout":
        return ["volume"]
    if atype == "null_spike":
        return [f"null_rate:{col}"] if col else ["null_rate"]
    if atype == "distribution_shift":
        return [f"distribution:{col}"] if col else ["distribution"]
    if atype == "loading_delay":
        return ["freshness"]
    if atype == "duplicate_rows":
        return ["volume", "pk_unique"]
    return []
