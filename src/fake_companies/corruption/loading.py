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

from ..config.schema import BaseScenarioConfig, SourceLoading
from ..core import RngHub
from ..core.calendar import Calendar
from ..output.schemas import TableSpec

__all__ = ["apply_loading", "event_reference"]

# Sane fallback when a schema has no source config: a simple ~30 min lag.
_DEFAULT_SOURCE = SourceLoading(cadence="streaming", lag_median_minutes=30.0, lag_sigma=0.5)


def event_reference(spec: TableSpec, df: pd.DataFrame, cal: Calendar) -> pd.Series:
    """Row-level business-event timestamp used as the loading anchor.

    Driven entirely by the spec: ``reference_data`` anchors at a single nominal
    timestamp, a tuple ``loading_ref`` coalesces left to right, otherwise the
    named column (falling back to ``event_time``, then ``created_at``).
    Returns a ``datetime64[ns]`` Series aligned to ``df.index``.
    """
    if spec.reference_data:
        return pd.Series(pd.Timestamp(cal.start), index=df.index)
    ref = spec.loading_ref
    if isinstance(ref, tuple):
        first, *rest = ref
        out = df[first]
        for col in rest:
            out = out.where(out.notna(), df[col])
        return pd.to_datetime(out)
    col = ref or spec.event_time or "created_at"
    return pd.to_datetime(df[col])


def _lag_minutes(
    gen: np.random.Generator, median_minutes: float, sigma: float, size: int
) -> np.ndarray:
    """Positive lognormal ingestion lag in minutes with the given median."""
    mu = np.log(max(median_minutes, 1e-9))
    return np.exp(mu + sigma * gen.standard_normal(size))


def apply_loading(
    cfg: BaseScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    frames: dict[str, pd.DataFrame],
    tables: list[TableSpec],
) -> None:
    """Fill ``_loaded_at`` on every raw frame present in ``frames`` (in place).

    ``tables`` (the vertical's raw specs) fixes the iteration order — it feeds
    RNG draw order, so it must be stable for determinism.
    """
    gen = rng.stream("loading")
    for spec in tables:
        fqn = spec.fqn
        df = frames.get(fqn)
        if df is None:
            continue

        src = cfg.loading.sources.get(spec.schema, _DEFAULT_SOURCE)
        ref = event_reference(spec, df, cal)
        lag = pd.to_timedelta(
            _lag_minutes(gen, src.lag_median_minutes, src.lag_sigma, len(df)), "m"
        )

        if src.cadence == "daily":
            next_morning = ref.dt.normalize() + pd.Timedelta(days=1) + pd.Timedelta(hours=6)
            loaded = next_morning + lag
        elif src.cadence == "weekly":
            # Weekly feeds (retailer POS): rows for a week land the morning
            # after the week ends. The event reference is the period start.
            week_after = ref.dt.normalize() + pd.Timedelta(days=7) + pd.Timedelta(hours=8)
            loaded = week_after + lag
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
