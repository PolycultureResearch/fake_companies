"""Connector loading model: fill every raw frame's ``_loaded_at`` column.

The loading layer is the *observation* clock. Business truth (the event-time
columns) is never touched; we only stamp when each row would have landed in the
warehouse given the source's connector cadence and a lognormal ingestion lag.

Cadences (per source, from ``cfg.loading.sources[schema]``):

- ``daily``: a nightly batch — rows for a given event day land the next morning
  around 06:00 plus a small lognormal delay.
- ``hourly`` / ``streaming``: ``_loaded_at = event_reference + lognormal_lag``.
- ``micro_batch``: the event time is first ceiled up to the next ``batch_minutes``
  boundary, then the lognormal lag is added.

``_loaded_at`` is guaranteed monotone: it is always ``>=`` the row's event
reference timestamp. All lag draws come from ``rng.stream("loading")``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.schema import ScenarioConfig, SourceLoading
from ..core import RngHub
from ..core.calendar import Calendar
from ..output.schemas import BY_FQN, RAW_TABLES

__all__ = ["apply_loading", "event_reference"]

# Sane fallback when a schema has no source config: a simple ~30 min lag.
_DEFAULT_SOURCE = SourceLoading(cadence="streaming", lag_median_minutes=30.0, lag_sigma=0.5)

# Event-reference column where the TableSpec has no ``event_time`` (or where the
# event_time column is a DATE that must be treated as midnight).
_REF_COL: dict[str, str] = {
    "ad_platform.ad_spend": "date",  # DATE -> midnight
    "app_db.users": "created_at",
}


def event_reference(fqn: str, df: pd.DataFrame, cal: Calendar) -> pd.Series:
    """Row-level business-event timestamp used as the loading anchor.

    Returns a ``datetime64[ns]`` Series aligned to ``df.index``.
    """
    if fqn == "app_db.subscriptions":
        # Coalesce paid start onto trial start.
        ref = df["started_at"].where(df["started_at"].notna(), df["trial_start_at"])
        return pd.to_datetime(ref)
    if fqn == "app_db.plans":
        # Plans are reference data: a single fixed early nominal timestamp.
        return pd.Series(pd.Timestamp(cal.start), index=df.index)

    spec = BY_FQN.get(fqn)
    col = _REF_COL.get(fqn) or (spec.event_time if spec is not None else None) or "created_at"
    return pd.to_datetime(df[col])


def _lag_minutes(
    gen: np.random.Generator, median_minutes: float, sigma: float, size: int
) -> np.ndarray:
    """Positive lognormal ingestion lag in minutes with the given median."""
    mu = np.log(max(median_minutes, 1e-9))
    return np.exp(mu + sigma * gen.standard_normal(size))


def apply_loading(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    frames: dict[str, pd.DataFrame],
) -> None:
    """Fill ``_loaded_at`` on every raw frame present in ``frames`` (in place)."""
    gen = rng.stream("loading")
    for spec in RAW_TABLES:  # fixed iteration order for determinism
        fqn = spec.fqn
        df = frames.get(fqn)
        if df is None:
            continue

        src = cfg.loading.sources.get(spec.schema, _DEFAULT_SOURCE)
        ref = event_reference(fqn, df, cal)
        lag = pd.to_timedelta(
            _lag_minutes(gen, src.lag_median_minutes, src.lag_sigma, len(df)), "m"
        )

        if src.cadence == "daily":
            next_morning = ref.dt.normalize() + pd.Timedelta(days=1) + pd.Timedelta(hours=6)
            loaded = next_morning + lag
        elif src.cadence == "micro_batch" and src.batch_minutes:
            midnight = ref.dt.normalize()
            steps = np.ceil((ref - midnight) / pd.Timedelta(minutes=src.batch_minutes))
            aligned = midnight + pd.to_timedelta(steps * src.batch_minutes, "m")
            loaded = aligned + lag
        else:  # hourly | streaming | micro_batch without batch_minutes
            loaded = ref + lag

        # Never let the observation clock precede the business event.
        loaded = loaded.where(loaded >= ref, ref)
        frames[fqn] = df.assign(_loaded_at=loaded.astype("datetime64[ns]").to_numpy())
