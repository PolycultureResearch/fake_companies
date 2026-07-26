"""The date grid and time-based masks shared by the latent layer.

A :class:`Calendar` is the canonical daily index for a scenario: it exposes the
date array plus day-of-week / day-of-year vectors and a holiday mask, all
aligned to the same length ``n_days``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from ..config.schema import ScenarioConfig


@dataclass(frozen=True)
class Calendar:
    start: dt.date
    end: dt.date

    @cached_property
    def dates(self) -> pd.DatetimeIndex:
        return pd.date_range(self.start, self.end, freq="D")

    @property
    def n_days(self) -> int:
        return len(self.dates)

    @cached_property
    def day_index(self) -> np.ndarray:
        """0-based day offset from ``start`` (float, for growth curves)."""
        return np.arange(self.n_days, dtype=float)

    @cached_property
    def dow(self) -> np.ndarray:
        """Day of week, Monday=0 .. Sunday=6."""
        return self.dates.dayofweek.to_numpy()

    @cached_property
    def doy(self) -> np.ndarray:
        """Day of year, 1..366."""
        return self.dates.dayofyear.to_numpy()

    def holiday_mask(self, country: str | None) -> np.ndarray:
        """Boolean array: True on public holidays in ``country`` (empty if None)."""
        mask = np.zeros(self.n_days, dtype=bool)
        if not country:
            return mask
        import holidays as holidays_lib

        years = range(self.start.year, self.end.year + 1)
        hols = holidays_lib.country_holidays(country, years=list(years))
        for i, ts in enumerate(self.dates):
            if ts.date() in hols:
                mask[i] = True
        return mask

    def date_to_index(self, date: dt.date) -> int:
        """Clamp a date to a valid 0-based index into the grid."""
        idx = (date - self.start).days
        return int(np.clip(idx, 0, self.n_days - 1))

    def window_slice(self, start: dt.date, end: dt.date | None) -> slice:
        """Half-open-safe slice covering [start, end] (end defaults to start)."""
        i0 = self.date_to_index(start)
        i1 = self.date_to_index(end) if end is not None else i0
        return slice(i0, i1 + 1)


def build_calendar(cfg: ScenarioConfig) -> Calendar:
    return Calendar(start=cfg.timeline.start, end=cfg.timeline.end_date)
