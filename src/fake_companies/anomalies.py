"""Resolve the full anomaly set (scripted + surprise-sampled) for a run.

Sampling happens once, deterministically, from a single ``anomalies.surprise``
RNG stream so the rate layer (M1) and the dq layer (M4) see a stable split by
kind. Each resolved anomaly also knows which downstream metrics / dataflow
signals it is expected to move — recorded in its :class:`GroundTruthRecord`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .config import ScenarioConfig
from .config.schema import ScriptedAnomaly, Window
from .core import RngHub
from .core.calendar import Calendar
from .latent.build import known_drivers

# Which raw columns a dq event can target, per table.
DQ_TABLE_META: dict[str, dict[str, list[str]]] = {
    "web.sessions": {
        "categorical": ["channel", "country", "device"],
        "nullable": ["user_id", "utm_campaign"],
        "numeric": ["duration_seconds", "page_views"],
    },
    "billing.payments": {
        "categorical": ["currency", "payment_method", "status"],
        "nullable": ["failure_code"],
        "numeric": ["amount"],
    },
    "product.events": {
        "categorical": ["event_name", "plan_at_event", "country", "device"],
        "nullable": [],
        "numeric": [],
    },
    "billing.invoices": {
        "categorical": ["currency", "status"],
        "nullable": [],
        "numeric": ["amount"],
    },
}

# Tables suitable as surprise-dq targets (Tremor dataflow "stars").
_DQ_SURPRISE_TABLES = ["product.events", "billing.payments", "web.sessions"]

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


def resolve_anomalies(cfg: ScenarioConfig, cal: Calendar, rng: RngHub) -> list[ResolvedAnomaly]:
    resolved = [ResolvedAnomaly(a, "scripted") for a in cfg.anomalies.scripted]
    if cfg.anomalies.surprise is not None:
        resolved.extend(_sample_surprise(cfg, cal, rng, resolved))
    return resolved


def rate_anomalies(resolved: list[ResolvedAnomaly]) -> list[ResolvedAnomaly]:
    return [r for r in resolved if r.spec.kind == "rate"]


def dq_anomalies(resolved: list[ResolvedAnomaly]) -> list[ResolvedAnomaly]:
    return [r for r in resolved if r.spec.kind == "dq"]


# --------------------------------------------------------------------------- #
# Surprise sampling
# --------------------------------------------------------------------------- #
def _sample_surprise(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    existing: list[ResolvedAnomaly],
) -> list[ResolvedAnomaly]:
    sc = cfg.anomalies.surprise
    assert sc is not None
    gen = rng.stream("anomalies.surprise")
    drivers = sorted(known_drivers(cfg))
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
        spec = _build_surprise_spec(kind, atype, start, magnitude, cal, gen, drivers)
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
        target = str(gen.choice(_DQ_SURPRISE_TABLES))
        meta = DQ_TABLE_META.get(target, {})
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
# Affected metrics / signals
# --------------------------------------------------------------------------- #
_DRIVER_METRICS: list[tuple[str, list[str]]] = [
    (
        "spend.",
        [
            "marketing_spend",
            "sessions",
            "signups",
            "trials_started",
            "new_subscriptions",
            "new_mrr",
            "mrr",
        ],
    ),
    ("sessions.", ["sessions", "signups", "trials_started", "new_subscriptions", "new_mrr", "mrr"]),
    (
        "signup_rate.",
        ["visit_signup_rate", "signups", "trials_started", "new_subscriptions", "new_mrr"],
    ),
    ("trial_start_rate", ["trials_started", "new_subscriptions", "new_mrr"]),
    ("trial_convert", ["trial_conversion_rate", "new_subscriptions", "new_mrr"]),
    (
        "churn.",
        [
            "churned_subscriptions",
            "customer_churn_rate",
            "churned_mrr",
            "active_subscriptions",
            "mrr",
        ],
    ),
    ("upgrade", ["expansion_mrr", "mrr"]),
    ("downgrade", ["contraction_mrr", "mrr"]),
    ("resurrect", ["new_subscriptions", "mrr"]),
    ("dau_over_active.", ["dau", "wau", "product_events"]),
    ("events_per_active_day.", ["product_events", "dau"]),
]


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["mrr"]


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
