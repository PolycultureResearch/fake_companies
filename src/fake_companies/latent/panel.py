"""The latent driver panel: daily rates per (driver, segment).

A :class:`DriverPanel` holds, for each named driver, a length-``n_days`` array of
non-negative daily rates. Drivers are stored topline (segment ``None``); a
segment-specific entry (e.g. ``{"device": "mobile"}``) may exist when an anomaly
confines its effect to a slice. Entity generators read a driver's rate and, when
they need a segment, fall back to topline if no segment entry exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.calendar import Calendar


def driver_key(name: str, segment: dict[str, str] | None = None) -> str:
    """Canonical string key for a (driver, segment) pair."""
    if not segment:
        return name
    seg = ",".join(f"{k}={v}" for k, v in sorted(segment.items()))
    return f"{name}|{seg}"


@dataclass
class DriverPanel:
    calendar: Calendar
    rates: dict[str, np.ndarray] = field(default_factory=dict)

    def set(self, name: str, values: np.ndarray, segment: dict[str, str] | None = None) -> None:
        arr = np.asarray(values, dtype=float)
        if arr.shape != (self.calendar.n_days,):
            raise ValueError(
                f"driver {name!r} array shape {arr.shape} != ({self.calendar.n_days},)"
            )
        self.rates[driver_key(name, segment)] = arr

    def has(self, name: str, segment: dict[str, str] | None = None) -> bool:
        return driver_key(name, segment) in self.rates

    def get(self, name: str, segment: dict[str, str] | None = None) -> np.ndarray:
        """Return the rate array for (name, segment), falling back to topline."""
        key = driver_key(name, segment)
        if key in self.rates:
            return self.rates[key]
        if name in self.rates:
            return self.rates[name]
        raise KeyError(f"no driver {key!r} (and no topline {name!r})")

    @property
    def names(self) -> list[str]:
        """Distinct topline driver names present in the panel."""
        return sorted({k.split("|", 1)[0] for k in self.rates})

    def copy(self) -> DriverPanel:
        return DriverPanel(self.calendar, {k: v.copy() for k, v in self.rates.items()})
