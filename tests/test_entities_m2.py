from __future__ import annotations

import numpy as np
import pytest

from fake_companies.anomalies import resolve_anomalies
from fake_companies.core import RngHub, build_calendar
from fake_companies.entities.funnel import build_users
from fake_companies.entities.marketing import build_ad_spend
from fake_companies.entities.plans import build_plan_index
from fake_companies.entities.traffic import build_sessions
from fake_companies.latent import apply_rate_events, build_drivers


@pytest.fixture
def built(smoke_cfg):
    cfg = smoke_cfg
    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed)
    resolved = resolve_anomalies(cfg, cal, rng)
    panel = build_drivers(cfg, cal, rng)
    panel, _ = apply_rate_events(panel, resolved, cfg, cal)
    ad = build_ad_spend(cfg, cal, rng, panel)
    sessions = build_sessions(cfg, cal, rng, panel)
    users = build_users(cfg, cal, rng, panel, sessions)
    return cfg, cal, panel, ad, sessions, users


def test_ad_spend_tracks_driver(built):
    cfg, cal, panel, ad, _, _ = built
    # Total daily spend across campaigns should track the spend driver.
    total_driver = panel.get("spend.paid_search").sum()
    total_spend = ad.loc[ad.channel == "paid_search", "spend"].sum()
    assert abs(total_spend / total_driver - 1.0) < 0.05


def test_sessions_poisson_tolerance(built):
    cfg, cal, panel, _, sessions, _ = built
    # Organic daily counts should sit within a wide Poisson band of the rate.
    rate = panel.get("sessions.organic")
    counts = (
        sessions[sessions.channel == "organic"]
        .groupby("day_index")
        .size()
        .reindex(range(cal.n_days), fill_value=0)
        .to_numpy()
    )
    # Mean count within 5% of mean rate over 90 days.
    assert abs(counts.mean() / rate.mean() - 1.0) < 0.05
    # ~95% of days within 4 sigma Poisson bounds.
    lo = rate - 4 * np.sqrt(rate)
    hi = rate + 4 * np.sqrt(rate)
    within = np.mean((counts >= lo) & (counts <= hi))
    assert within > 0.9


def test_weekend_dip_in_sessions(built):
    cfg, cal, _, _, sessions, _ = built
    per_day = sessions.groupby("day_index").size()
    cal_dow = cal.dow
    counts = per_day.reindex(range(cal.n_days), fill_value=0).to_numpy()
    weekday = counts[np.isin(cal_dow, [0, 1, 2, 3, 4])].mean()
    weekend = counts[np.isin(cal_dow, [5, 6])].mean()
    assert weekend < weekday


def test_signup_rate_within_binomial_ci(built):
    cfg, cal, panel, _, sessions, users = built
    # Per-channel realized signup rate ~ configured rate (wide binomial CI).
    for ch, cfg_rate in cfg.funnel.signup_rate.items():
        sess_ch = sessions[sessions.channel == ch]
        n = len(sess_ch)
        if n < 500:
            continue
        k = int(sess_ch["user_id"].notna().sum())
        phat = k / n
        se = (cfg_rate * (1 - cfg_rate) / n) ** 0.5
        assert abs(phat - cfg_rate) < 5 * se, (ch, phat, cfg_rate)


def test_users_reference_valid_sessions(built):
    cfg, cal, _, _, sessions, users = built
    # Every user id assigned to a session appears exactly once, and matches users.
    assigned = sessions["user_id"].dropna().astype("int64")
    assert assigned.is_unique
    assert set(assigned) == set(users["user_id"])
    assert users["user_id"].is_unique
    assert users["email"].is_unique


def test_plan_index_monthly_price():
    from fake_companies.config import load_config

    cfg = load_config("configs/acme_b2c_saas.yaml")
    idx = build_plan_index(cfg)
    annual_pro = idx.id_for("pro", "annual")
    # annual pro is 490/yr -> ~40.83/mo MRR contribution
    assert abs(idx.monthly_price(annual_pro) - 490 / 12) < 1e-6
    assert idx.monthly_price(idx.id_for("basic", "monthly")) == 15
