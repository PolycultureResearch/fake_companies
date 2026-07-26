from __future__ import annotations

import datetime as dt

import numpy as np

from fake_companies.config.schema import GrowthConfig, PiecewiseSegment
from fake_companies.core import RngHub, growth_curve
from fake_companies.core.calendar import Calendar


def _cal(days: int = 100) -> Calendar:
    start = dt.date(2024, 1, 1)
    return Calendar(start=start, end=start + dt.timedelta(days=days - 1))


# --- RNG determinism -------------------------------------------------------- #
def test_rng_same_seed_same_stream():
    a = RngHub(42).stream("traffic").normal(size=1000)
    b = RngHub(42).stream("traffic").normal(size=1000)
    assert np.array_equal(a, b)


def test_rng_streams_independent():
    hub = RngHub(42)
    a = hub.stream("traffic").normal(size=1000)
    b = hub.stream("billing").normal(size=1000)
    assert not np.array_equal(a, b)


def test_rng_new_stream_does_not_perturb_existing():
    # Adding a stream must not change another stream's draws (order-independence).
    hub1 = RngHub(1)
    first = hub1.stream("a").integers(0, 100, size=50)
    hub2 = RngHub(1)
    hub2.stream("z").integers(0, 100, size=50)  # touch a different stream first
    second = hub2.stream("a").integers(0, 100, size=50)
    assert np.array_equal(first, second)


def test_rng_seed_changes_output():
    a = RngHub(1).stream("x").normal(size=100)
    b = RngHub(2).stream("x").normal(size=100)
    assert not np.array_equal(a, b)


# --- Growth curves ---------------------------------------------------------- #
def test_growth_flat():
    cal = _cal()
    g = growth_curve(GrowthConfig(kind="flat"), cal)
    assert np.allclose(g, 1.0)


def test_growth_starts_at_one():
    cal = _cal()
    for cfg in (
        GrowthConfig(kind="linear", rate=0.01),
        GrowthConfig(kind="exponential", rate=0.005),
        GrowthConfig(kind="logistic", capacity=4.0, steepness=0.05),
    ):
        g = growth_curve(cfg, cal)
        assert abs(g[0] - 1.0) < 1e-9


def test_growth_exponential_monotonic():
    cal = _cal()
    g = growth_curve(GrowthConfig(kind="exponential", rate=0.01), cal)
    assert np.all(np.diff(g) > 0)
    assert g[-1] > g[0]


def test_growth_logistic_approaches_capacity():
    cal = _cal(400)
    g = growth_curve(GrowthConfig(kind="logistic", capacity=5.0, steepness=0.05), cal)
    assert g[-1] > 4.5  # nearing the ceiling
    assert g[-1] <= 5.01


def test_growth_piecewise_continuous():
    cal = _cal(90)
    cfg = GrowthConfig(
        kind="piecewise",
        segments=[
            PiecewiseSegment(start=dt.date(2024, 1, 1), kind="linear", rate=0.01),
            PiecewiseSegment(start=dt.date(2024, 2, 1), kind="flat"),
        ],
    )
    g = growth_curve(cfg, cal)
    assert abs(g[0] - 1.0) < 1e-9
    assert np.all(g > 0)
    # Second segment is flat, so growth plateaus after the breakpoint.
    assert abs(g[-1] - g[35]) < 0.2


# --- Calendar --------------------------------------------------------------- #
def test_calendar_shape_and_dow():
    cal = _cal(10)
    assert cal.n_days == 10
    assert cal.dow[0] == 0  # 2024-01-01 is a Monday
    assert cal.doy[0] == 1


def test_holiday_mask_new_years():
    cal = _cal(10)
    mask = cal.holiday_mask("US")
    assert mask[0]  # 2024-01-01 New Year's Day
    assert not mask[5]
