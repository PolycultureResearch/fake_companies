"""Shared, vectorized helpers for entity generators.

Everything here is deterministic given the ``numpy.random.Generator`` passed in.
No entity should loop per-row in Python; these helpers expand latent daily rates
into row-level arrays with numpy.
"""

from __future__ import annotations

import numpy as np

# Diurnal weights for web sessions (local hour 0..23): quiet overnight, business
# hours plateau, mild evening tail.
SESSION_HOUR_WEIGHTS = np.array(
    [
        0.2,
        0.1,
        0.1,
        0.1,
        0.15,
        0.3,
        0.6,
        1.2,
        2.0,
        2.6,
        3.0,
        3.1,
        3.0,
        3.0,
        2.9,
        2.8,
        2.6,
        2.4,
        2.2,
        2.0,
        1.7,
        1.3,
        0.8,
        0.4,
    ],
    dtype=float,
)

# Diurnal weights for product usage: heavier in the evening (B2C).
USAGE_HOUR_WEIGHTS = np.array(
    [
        0.4,
        0.2,
        0.15,
        0.1,
        0.1,
        0.2,
        0.5,
        1.0,
        1.6,
        2.0,
        2.2,
        2.3,
        2.2,
        2.1,
        2.0,
        2.0,
        2.1,
        2.4,
        2.9,
        3.2,
        3.1,
        2.6,
        1.8,
        0.9,
    ],
    dtype=float,
)


def poisson_counts(gen: np.random.Generator, rates: np.ndarray) -> np.ndarray:
    """Draw independent Poisson counts for an array of daily rates."""
    return gen.poisson(np.maximum(rates, 0.0)).astype(np.int64)


def expand_day_index(counts: np.ndarray) -> np.ndarray:
    """Row-per-event day indices from per-day counts (np.repeat of arange)."""
    return np.repeat(np.arange(len(counts), dtype=np.int64), counts)


def sample_labels(
    gen: np.random.Generator, labels: list[str], probs: list[float], size: int
) -> np.ndarray:
    """Vectorized categorical draw returning an object array of label strings."""
    p = np.asarray(probs, dtype=float)
    p = p / p.sum()
    idx = gen.choice(len(labels), size=size, p=p)
    return np.asarray(labels, dtype=object)[idx]


def intraday_seconds(
    gen: np.random.Generator, day_index: np.ndarray, hour_weights: np.ndarray
) -> np.ndarray:
    """Second-of-day offsets sampled from a diurnal hour distribution."""
    n = len(day_index)
    w = hour_weights / hour_weights.sum()
    hours = gen.choice(24, size=n, p=w)
    secs_in_hour = gen.integers(0, 3600, size=n)
    return hours * 3600 + secs_in_hour


def timestamps_from_days(
    start_date, day_index: np.ndarray, second_offsets: np.ndarray
) -> np.ndarray:
    """Build datetime64[s] timestamps from a start date + day + second offsets."""
    base = np.datetime64(start_date, "s")
    day_secs = day_index.astype("int64") * 86400
    return base + (day_secs + second_offsets.astype("int64")).astype("timedelta64[s]")


def lognormal_around(gen: np.random.Generator, mean: float, sigma: float, size: int) -> np.ndarray:
    """Lognormal draws with the given arithmetic mean and log-sigma."""
    mu = np.log(max(mean, 1e-9)) - 0.5 * sigma**2
    return gen.lognormal(mu, sigma, size=size)
