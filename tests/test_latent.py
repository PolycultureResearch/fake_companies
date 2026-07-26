from __future__ import annotations

import datetime as dt

import numpy as np

from fake_companies.anomalies import resolve_anomalies
from fake_companies.config import load_config
from fake_companies.core import RngHub, build_calendar
from fake_companies.latent import apply_rate_events, build_drivers, known_drivers


def _setup(cfg):
    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed)
    return cal, rng


def test_drivers_present_and_positive(smoke_cfg):
    cal, rng = _setup(smoke_cfg)
    panel = build_drivers(smoke_cfg, cal, rng)
    expected = known_drivers(smoke_cfg)
    assert set(panel.names) == expected
    for name in panel.names:
        arr = panel.get(name)
        assert arr.shape == (cal.n_days,)
        assert np.all(arr >= 0)


def test_probability_drivers_bounded(smoke_cfg):
    cal, rng = _setup(smoke_cfg)
    panel = build_drivers(smoke_cfg, cal, rng)
    for name in panel.names:
        if name.startswith(("signup_rate.", "churn.", "dau_over_active.")) or name in (
            "trial_start_rate",
            "trial_convert",
            "upgrade",
            "downgrade",
            "resurrect",
        ):
            assert np.all(panel.get(name) <= 1.0)


def test_noise_mean_near_baseline(smoke_cfg):
    # AR(1) lognormal noise is de-biased to mean ~1, so a flat driver stays ~baseline.
    cal, rng = _setup(smoke_cfg)
    panel = build_drivers(smoke_cfg, cal, rng)
    spend = panel.get("spend.paid_search")
    # paid_search spend baseline 300, flat growth, seasonality mean ~1.
    assert 0.9 < spend.mean() / 300.0 < 1.1


def test_weekend_dip_present(smoke_cfg):
    cal, rng = _setup(smoke_cfg)
    panel = build_drivers(smoke_cfg, cal, rng)
    organic = panel.get("sessions.organic")
    weekday = organic[np.isin(cal.dow, [0, 1, 2, 3, 4])].mean()
    weekend = organic[np.isin(cal.dow, [5, 6])].mean()
    assert weekend < weekday  # configured weekly_shape dips on weekends


def test_determinism_same_seed(smoke_cfg):
    cal, rng1 = _setup(smoke_cfg)
    p1 = build_drivers(smoke_cfg, cal, rng1)
    p2 = build_drivers(smoke_cfg, cal, RngHub(smoke_cfg.seed))
    for name in p1.names:
        assert np.array_equal(p1.get(name), p2.get(name))


def test_level_shift_changes_windowed_mean(smoke_cfg):
    cal, rng = _setup(smoke_cfg)
    resolved = resolve_anomalies(smoke_cfg, cal, rng)
    clean = build_drivers(smoke_cfg, cal, RngHub(smoke_cfg.seed))
    dirty = build_drivers(smoke_cfg, cal, RngHub(smoke_cfg.seed))
    dirty, records = apply_rate_events(dirty, resolved, smoke_cfg, cal)

    # smoke config injects a spend.paid_search level_shift x0.6 over 2024-02-01..14.
    rec = next(r for r in records if r.target == "spend.paid_search")
    sl = cal.window_slice(rec.start, rec.end)
    ratio = dirty.get("spend.paid_search")[sl].mean() / clean.get("spend.paid_search")[sl].mean()
    assert abs(ratio - 0.6) < 0.02  # within +-2%
    # Outside the window the driver is unchanged.
    assert np.allclose(dirty.get("spend.paid_search")[:10], clean.get("spend.paid_search")[:10])


def test_ground_truth_emitted_for_each_rate_event(smoke_cfg):
    cal, rng = _setup(smoke_cfg)
    resolved = resolve_anomalies(smoke_cfg, cal, rng)
    panel = build_drivers(smoke_cfg, cal, rng)
    _, records = apply_rate_events(panel, resolved, smoke_cfg, cal)
    n_rate = sum(1 for r in resolved if r.spec.kind == "rate")
    assert len(records) == n_rate
    for rec in records:
        assert rec.affected_metrics  # every event names its expected downstream metrics


def test_segmented_event_leaves_topline_clean(acme_config_path):
    cfg = load_config(acme_config_path)
    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed)
    resolved = resolve_anomalies(cfg, cal, rng)
    clean = build_drivers(cfg, cal, RngHub(cfg.seed))
    dirty = build_drivers(cfg, cal, RngHub(cfg.seed))
    dirty, _ = apply_rate_events(dirty, resolved, cfg, cal)
    # paid_social signup regression is confined to {US, mobile}; topline unchanged.
    seg = {"country": "US", "device": "mobile"}
    assert np.allclose(dirty.get("signup_rate.paid_social"), clean.get("signup_rate.paid_social"))
    assert dirty.has("signup_rate.paid_social", seg)
    assert not np.allclose(
        dirty.get("signup_rate.paid_social", seg), clean.get("signup_rate.paid_social")
    )


def test_trend_change_is_monotone_decline(smoke_cfg):
    cal, _rng = _setup(smoke_cfg)
    from fake_companies.config.schema import Window
    from fake_companies.latent import build_rate_multiplier

    mult = build_rate_multiplier(
        "trend_change", cal, Window(start=dt.date(2024, 1, 15)), 0.99, None
    )
    assert mult[0] == 1.0
    tail = mult[14:]
    assert np.all(np.diff(tail) <= 0)  # compounding decline after the breakpoint
    assert tail[-1] < 1.0
