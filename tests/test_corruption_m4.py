"""M4 observation-layer tests: loading model + dq corruptions.

The full ``generate()`` in this worktree does not yet emit every raw frame (the
usage module is built in a sibling milestone), so we exercise the loading model
and each dq corruption on small in-memory frames that match the relevant
schemas. Each corruption is measured with the SAME statistic Tremor uses and we
assert a measurable shift in an anomaly window vs a clean baseline window.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from fake_companies.config.schema import LoadingConfig, ScriptedAnomaly, SourceLoading, Window
from fake_companies.core import RngHub
from fake_companies.core.calendar import Calendar
from fake_companies.corruption import apply_loading_and_dq
from fake_companies.corruption.dq import (
    distribution_shift,
    duplicate_rows,
    in_window_mask,
    loading_delay,
    null_spike,
    volume_dropout,
)
from fake_companies.corruption.loading import apply_loading, event_reference

try:  # anomalies.py is a sibling-milestone deliverable; fall back locally.
    from fake_companies.anomalies import ResolvedAnomaly
except ImportError:  # pragma: no cover
    from dataclasses import dataclass

    @dataclass
    class ResolvedAnomaly:  # type: ignore[no-redef]
        spec: ScriptedAnomaly
        origin: str


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #
START = dt.date(2024, 1, 1)
BASE_WIN = Window(start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 6))  # clean baseline
ANOM_WIN = Window(start=dt.date(2024, 1, 9), end=dt.date(2024, 1, 13))  # corrupted
PAYMENTS = "billing.payments"

CAL = Calendar(start=START, end=dt.date(2024, 1, 14))
N_PER_DAY = 300


def _rng() -> RngHub:
    return RngHub(1234)


def make_payments() -> pd.DataFrame:
    """A billing.payments-like frame: 14 days x N_PER_DAY rows, no _loaded_at."""
    gen = np.random.default_rng(0)
    n_days = 14
    n = n_days * N_PER_DAY
    day = np.repeat(np.arange(n_days), N_PER_DAY)
    secs = gen.integers(0, 86400, size=n)
    created_at = pd.Timestamp(START) + pd.to_timedelta(day * 86400 + secs, unit="s")
    currency = np.asarray(["USD", "EUR", "GBP"], dtype=object)[
        gen.choice(3, size=n, p=[0.90, 0.07, 0.03])
    ]
    return pd.DataFrame(
        {
            "payment_id": np.arange(n, dtype=np.int64),
            "invoice_id": np.arange(n, dtype=np.int64),
            "user_id": gen.integers(1, 5000, size=n),
            "amount": gen.uniform(5, 100, size=n).round(2),
            "currency": currency,
            "payment_method": "card",
            "status": "succeeded",
            "failure_code": np.array(["ok"] * n, dtype=object),  # baseline: non-null
            "created_at": created_at,
        }
    )


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        loading=LoadingConfig(
            sources={"billing": SourceLoading(cadence="streaming", lag_median_minutes=30.0)}
        )
    )


def _daily_counts(df: pd.DataFrame, win: Window) -> float:
    mask = in_window_mask(df, PAYMENTS, CAL, win)
    days = (win.end - win.start).days + 1
    return int(mask.sum()) / days


def _null_rate(df: pd.DataFrame, col: str, win: Window) -> float:
    mask = in_window_mask(df, PAYMENTS, CAL, win)
    sub = df.loc[mask, col]
    return float(sub.isna().mean())


def _mean_lag_minutes(df: pd.DataFrame, win: Window) -> float:
    mask = in_window_mask(df, PAYMENTS, CAL, win)
    ref = event_reference(PAYMENTS, df, CAL)
    lag = (pd.to_datetime(df["_loaded_at"]) - ref).dt.total_seconds() / 60.0
    return float(lag[mask].mean())


def _dist(df: pd.DataFrame, col: str, win: Window) -> pd.Series:
    mask = in_window_mask(df, PAYMENTS, CAL, win)
    return df.loc[mask, col].value_counts(normalize=True)


def _psi(p_win: pd.Series, p_base: pd.Series, eps: float = 1e-4) -> float:
    cats = p_win.index.union(p_base.index)
    a = p_win.reindex(cats, fill_value=0.0).to_numpy() + eps
    b = p_base.reindex(cats, fill_value=0.0).to_numpy() + eps
    a = a / a.sum()
    b = b / b.sum()
    return float(np.sum((a - b) * np.log(a / b)))


# --------------------------------------------------------------------------- #
# Loading model
# --------------------------------------------------------------------------- #
def test_loading_stamps_loaded_at_after_event_time():
    df = make_payments()
    frames = {PAYMENTS: df}
    apply_loading(_cfg(), CAL, _rng(), frames)
    out = frames[PAYMENTS]
    assert "_loaded_at" in out.columns
    ref = event_reference(PAYMENTS, out, CAL)
    assert (pd.to_datetime(out["_loaded_at"]) >= ref).all()


def test_loading_daily_cadence_lands_next_morning():
    # ad_spend-like frame: DATE event column, daily batch.
    dates = pd.to_datetime([START, START, dt.date(2024, 1, 5)])
    df = pd.DataFrame({"spend_id": [1, 2, 3], "date": dates.date, "spend": [1.0, 2.0, 3.0]})
    frames = {"ad_platform.ad_spend": df}
    cfg = SimpleNamespace(
        loading=LoadingConfig(sources={"ad_platform": SourceLoading(cadence="daily")})
    )
    apply_loading(cfg, CAL, _rng(), frames)
    out = frames["ad_platform.ad_spend"]
    loaded = pd.to_datetime(out["_loaded_at"])
    ref = pd.to_datetime(out["date"])
    assert (loaded >= ref).all()
    # Next-morning batch: at least the following day.
    assert (loaded.dt.normalize() > ref.dt.normalize()).all()


def test_loading_default_source_when_schema_missing():
    df = make_payments()
    frames = {PAYMENTS: df}
    cfg = SimpleNamespace(loading=LoadingConfig(sources={}))  # no 'billing' key
    apply_loading(cfg, CAL, _rng(), frames)
    ref = event_reference(PAYMENTS, frames[PAYMENTS], CAL)
    assert (pd.to_datetime(frames[PAYMENTS]["_loaded_at"]) >= ref).all()


# --------------------------------------------------------------------------- #
# DQ corruptions (measured with Tremor-style stats)
# --------------------------------------------------------------------------- #
def test_volume_dropout():
    df = make_payments()
    mag = 0.3
    mask = in_window_mask(df, PAYMENTS, CAL, ANOM_WIN)
    out = volume_dropout(df, mask, mag, _rng().stream("dq"))
    base = _daily_counts(out, BASE_WIN)
    win = _daily_counts(out, ANOM_WIN)
    assert base == pytest.approx(N_PER_DAY, rel=0.05)
    assert win / base == pytest.approx(mag, abs=0.08)


def test_null_spike():
    df = make_payments()
    mag = 0.25
    mask = in_window_mask(df, PAYMENTS, CAL, ANOM_WIN)
    out = null_spike(df, mask, mag, "failure_code", _rng().stream("dq"))
    base = _null_rate(out, "failure_code", BASE_WIN)
    win = _null_rate(out, "failure_code", ANOM_WIN)
    assert base == pytest.approx(0.0, abs=1e-9)
    assert (win - base) == pytest.approx(mag, abs=0.06)


def test_distribution_shift_psi():
    df = make_payments()
    params = {"column": "currency", "new_mix": {"EUR": 0.6, "USD": 0.25, "GBP": 0.15}}
    mask = in_window_mask(df, PAYMENTS, CAL, ANOM_WIN)
    out = distribution_shift(df, mask, params, _rng().stream("dq"))
    base = _dist(out, "currency", BASE_WIN)
    win = _dist(out, "currency", ANOM_WIN)
    assert _psi(win, base) > 0.25


def test_loading_delay():
    df = make_payments()
    frames = {PAYMENTS: df}
    apply_loading(_cfg(), CAL, _rng(), frames)
    mag = 6.0
    df = frames[PAYMENTS]
    baseline_lag = _mean_lag_minutes(df, ANOM_WIN)  # pre-corruption lag in the window
    mask = in_window_mask(df, PAYMENTS, CAL, ANOM_WIN)
    out = loading_delay(df, mask, mag, PAYMENTS, CAL)
    ref = event_reference(PAYMENTS, out, CAL)
    assert (pd.to_datetime(out["_loaded_at"]) >= ref).all()
    win_lag = _mean_lag_minutes(out, ANOM_WIN)
    assert win_lag / baseline_lag == pytest.approx(mag, rel=0.02)


def test_duplicate_rows():
    df = make_payments()
    mag = 0.5
    n_before = len(df)
    mask = in_window_mask(df, PAYMENTS, CAL, ANOM_WIN)
    in_win = int(mask.sum())
    out = duplicate_rows(df, mask, mag, _rng().stream("dq"))
    added = len(out) - n_before
    assert added / in_win == pytest.approx(mag, abs=0.08)
    # True duplicate primary keys now exist.
    assert out["payment_id"].duplicated().any()


# --------------------------------------------------------------------------- #
# End-to-end: ground truth
# --------------------------------------------------------------------------- #
def _dq_specs() -> list[ResolvedAnomaly]:
    return [
        ResolvedAnomaly(
            ScriptedAnomaly(
                name="drop_vol",
                kind="dq",
                type="volume_dropout",
                target=PAYMENTS,
                window=ANOM_WIN,
                magnitude=0.3,
            ),
            "scripted",
        ),
        ResolvedAnomaly(
            ScriptedAnomaly(
                name="nulls",
                kind="dq",
                type="null_spike",
                target=PAYMENTS,
                window=ANOM_WIN,
                magnitude=0.25,
                params={"column": "failure_code"},
            ),
            "scripted",
        ),
        ResolvedAnomaly(
            ScriptedAnomaly(
                name="shift",
                kind="dq",
                type="distribution_shift",
                target=PAYMENTS,
                window=ANOM_WIN,
                magnitude=1.0,
                params={"column": "currency", "new_mix": {"EUR": 0.6, "USD": 0.25, "GBP": 0.15}},
            ),
            "scripted",
        ),
        ResolvedAnomaly(
            ScriptedAnomaly(
                name="delay",
                kind="dq",
                type="loading_delay",
                target=PAYMENTS,
                window=ANOM_WIN,
                magnitude=6.0,
            ),
            "scripted",
        ),
        ResolvedAnomaly(
            ScriptedAnomaly(
                name="dupes",
                kind="dq",
                type="duplicate_rows",
                target=PAYMENTS,
                window=ANOM_WIN,
                magnitude=0.5,
            ),
            "scripted",
        ),
    ]


def test_apply_loading_and_dq_emits_ground_truth():
    frames = {PAYMENTS: make_payments()}
    resolved = _dq_specs()
    records = apply_loading_and_dq(_cfg(), CAL, _rng(), frames, resolved)
    assert len(records) == len(resolved)
    for rec in records:
        assert rec.kind == "dq"
        assert rec.target == PAYMENTS
        assert rec.affected_signals, f"empty affected_signals for {rec.type}"


def test_apply_loading_and_dq_without_resolved_still_loads():
    frames = {PAYMENTS: make_payments()}
    records = apply_loading_and_dq(_cfg(), CAL, _rng(), frames)
    assert records == []
    ref = event_reference(PAYMENTS, frames[PAYMENTS], CAL)
    assert (pd.to_datetime(frames[PAYMENTS]["_loaded_at"]) >= ref).all()
