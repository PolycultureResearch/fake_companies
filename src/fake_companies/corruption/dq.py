"""Data-quality corruptions on the observation layer.

Each corruption mutates only the *observed* rows of a raw frame over an anomaly
window; the business truth (event-time columns, entity counts in the latent
layer) is unchanged. Every dq anomaly emits exactly one
:class:`GroundTruthRecord` so a detector can be scored against it.

Corruptions are measurable with the same statistics Tremor computes:

- ``volume_dropout``  -> volume (row count) falls
- ``null_spike``      -> null-rate of a column rises
- ``distribution_shift`` -> categorical PSI shifts
- ``loading_delay``   -> freshness lag (``_loaded_at`` - event) grows
- ``duplicate_rows``  -> volume rises and primary-key uniqueness breaks

All random draws come from ``rng.stream("dq")``, consumed in a fixed iteration
order over ``dq_anomalies(resolved)`` for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..anomalies import affected_signals_for_dq, dq_anomalies
from ..config.schema import BaseScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..groundtruth import GroundTruthRecord
from ..output.schemas import TableSpec
from .loading import event_reference

__all__ = [
    "apply_dq",
    "distribution_shift",
    "duplicate_rows",
    "in_window_mask",
    "loading_delay",
    "null_spike",
    "volume_dropout",
]


def in_window_mask(df: pd.DataFrame, spec: TableSpec, cal: Calendar, window) -> np.ndarray:
    """Boolean mask of rows whose event day falls within ``window``."""
    ref_day = event_reference(spec, df, cal).dt.normalize()
    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end if window.end is not None else window.start)
    return ((ref_day >= start) & (ref_day <= end)).to_numpy()


# --------------------------------------------------------------------------- #
# Individual corruptions (each returns a new frame)
# --------------------------------------------------------------------------- #
def volume_dropout(
    df: pd.DataFrame, mask: np.ndarray, magnitude: float, gen: np.random.Generator
) -> pd.DataFrame:
    """Keep only ~``magnitude`` of in-window rows; drop the rest at random."""
    keep = ~mask | (gen.random(len(df)) < magnitude)
    return df[keep].reset_index(drop=True)


def null_spike(
    df: pd.DataFrame, mask: np.ndarray, magnitude: float, column: str, gen: np.random.Generator
) -> pd.DataFrame:
    """Null out ``column`` for ~``magnitude`` of in-window rows."""
    df = df.copy()
    hit = mask & (gen.random(len(df)) < magnitude)
    df.loc[hit, column] = np.nan
    return df


def distribution_shift(
    df: pd.DataFrame, mask: np.ndarray, params: dict, gen: np.random.Generator
) -> pd.DataFrame:
    """Reassign a categorical column's in-window values.

    Uses ``params['new_mix']`` (a ``{value: prob}`` dict) if present, otherwise
    concentrates mass onto the current top category with probability
    ``params.get('skew', 0.6)``.
    """
    df = df.copy()
    column = params["column"]
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return df

    col_pos = df.columns.get_loc(column)
    new_mix = params.get("new_mix")
    if new_mix:
        values = list(new_mix.keys())
        probs = np.asarray(list(new_mix.values()), dtype=float)
        probs = probs / probs.sum()
        draws = gen.choice(len(values), size=idx.size, p=probs)
        df.iloc[idx, col_pos] = np.asarray(values, dtype=object)[draws]
    else:
        skew = float(params.get("skew", 0.6))
        win_vals = df[column].to_numpy()[idx]
        top = pd.Series(win_vals).value_counts().idxmax()
        r = gen.random(idx.size)
        df.iloc[idx, col_pos] = np.where(r < skew, top, win_vals)
    return df


def loading_delay(
    df: pd.DataFrame, mask: np.ndarray, magnitude: float, spec: TableSpec, cal: Calendar
) -> pd.DataFrame:
    """Multiply the freshness lag of in-window rows by ``magnitude``."""
    df = df.copy()
    ref = event_reference(spec, df, cal)
    lag = pd.to_datetime(df["_loaded_at"]) - ref
    new_loaded = ref + lag * magnitude
    df["_loaded_at"] = pd.to_datetime(df["_loaded_at"]).where(~mask, new_loaded)
    return df


def duplicate_rows(
    df: pd.DataFrame, mask: np.ndarray, magnitude: float, gen: np.random.Generator
) -> pd.DataFrame:
    """Append true duplicate copies (same PK) of ~``magnitude`` of in-window rows."""
    dup = mask & (gen.random(len(df)) < magnitude)
    dups = df[dup].copy()
    return pd.concat([df, dups], ignore_index=True)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
def apply_dq(
    cfg: BaseScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    frames: dict[str, pd.DataFrame],
    resolved: list,
    tables: list[TableSpec],
) -> list[GroundTruthRecord]:
    """Apply every dq anomaly and return one GroundTruthRecord per anomaly."""
    by_fqn = {t.fqn: t for t in tables}
    gen = rng.stream("dq")
    records: list[GroundTruthRecord] = []
    for r in dq_anomalies(resolved):
        spec = r.spec
        fqn = spec.target
        params = dict(spec.params or {})
        df = frames.get(fqn)
        table = by_fqn.get(fqn)

        if df is not None and len(df) and table is not None:
            mask = in_window_mask(df, table, cal, spec.window)
            if spec.type == "volume_dropout":
                frames[fqn] = volume_dropout(df, mask, spec.magnitude, gen)
            elif spec.type == "null_spike":
                frames[fqn] = null_spike(df, mask, spec.magnitude, params["column"], gen)
            elif spec.type == "distribution_shift":
                frames[fqn] = distribution_shift(df, mask, params, gen)
            elif spec.type == "loading_delay":
                frames[fqn] = loading_delay(df, mask, spec.magnitude, table, cal)
            elif spec.type == "duplicate_rows":
                frames[fqn] = duplicate_rows(df, mask, spec.magnitude, gen)

        records.append(
            GroundTruthRecord(
                id=spec.name,
                kind="dq",
                type=spec.type,
                origin=r.origin,
                target=fqn,
                segment=spec.segment,
                start=spec.window.start,
                end=spec.window.end,
                magnitude=spec.magnitude,
                affected_signals=affected_signals_for_dq(spec.type, params),
                params=params,
            )
        )
    return records
