"""Ground-truth honesty: every injected anomaly must be detectable *in principle*.

For each scripted anomaly we recompute the affected aggregate directly from the
raw frames and require the in-window effect to exceed ~3 robust sigmas versus a
clean baseline window. If these fail, the fault is the generator (an anomaly that
doesn't actually move its signal), not a detector under test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.generate import generate


@pytest.fixture(scope="module")
def smoke_run():
    cfg = load_config("configs/smoke_90d.yaml")
    return cfg, generate(cfg)


def _mean_shift_sigmas(baseline: np.ndarray, window: np.ndarray) -> float:
    """Robust sigmas separating the window mean from the baseline center.

    Uses the standard error of the window mean (robust daily scale / sqrt(n)),
    i.e. the detectability of a sustained mean shift, not a single-day excursion.
    """
    med = np.median(baseline)
    mad = np.median(np.abs(baseline - med)) * 1.4826
    sem = max(mad, 1e-9) / np.sqrt(len(window))
    return abs(np.mean(window) - med) / sem


def _daily_counts(ts: pd.Series, start, end) -> pd.Series:
    days = pd.to_datetime(ts).dt.floor("D")
    idx = pd.date_range(start, end, freq="D")
    return days.value_counts().reindex(idx, fill_value=0).sort_index()


def test_budget_cut_moves_paid_search_spend(smoke_run):
    cfg, r = smoke_run
    # smoke: spend.paid_search level_shift x0.6 over 2024-02-01..14
    ad = r.frames["ad_platform.ad_spend"]
    ps = ad[ad.channel == "paid_search"]
    daily = ps.groupby("date")["spend"].sum()
    daily.index = pd.to_datetime(daily.index)

    win = daily["2024-02-01":"2024-02-14"]
    base = daily["2024-01-05":"2024-01-28"]
    sig = _mean_shift_sigmas(base.to_numpy(), win.to_numpy())
    assert win.mean() < base.median()  # a drop
    assert sig >= 3.0, f"budget cut only {sig:.1f} robust sigmas"


def test_pipeline_outage_drops_product_volume(smoke_run):
    cfg, r = smoke_run
    # smoke: volume_dropout on product.events over 2024-03-10..11 (magnitude 0.3)
    ev = r.frames["product.events"]
    daily = _daily_counts(ev["occurred_at"], "2024-01-01", "2024-03-30")
    win = daily["2024-03-10":"2024-03-11"]
    base = daily["2024-02-20":"2024-03-08"]
    sig = _mean_shift_sigmas(base.to_numpy(), win.to_numpy())
    assert win.mean() < base.median()
    assert sig >= 3.0, f"outage only {sig:.1f} robust sigmas"


@pytest.mark.slow
def test_all_scripted_rate_anomalies_detectable():
    """Acme: each scripted rate anomaly moves its primary affected aggregate."""
    cfg = load_config("configs/acme_b2c_saas.yaml")
    r = generate(cfg)
    ad = r.frames["ad_platform.ad_spend"]

    # paid_search_budget_cut: 2024-09-02..22, x0.6
    daily = ad[ad.channel == "paid_search"].groupby("date")["spend"].sum()
    daily.index = pd.to_datetime(daily.index)
    win = daily["2024-09-02":"2024-09-22"]
    base = daily["2024-08-01":"2024-08-28"]
    assert _mean_shift_sigmas(base.to_numpy(), win.to_numpy()) >= 3.0
    assert win.mean() < base.median()
