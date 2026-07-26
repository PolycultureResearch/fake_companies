from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.entities.plans import build_plan_index
from fake_companies.generate import generate


@pytest.fixture(scope="module")
def smoke_run():
    cfg = load_config("configs/smoke_90d.yaml")
    return cfg, generate(cfg)


def _mrr(idx, pid):
    return 0.0 if pd.isna(pid) else idx.monthly_price(int(pid))


def test_mrr_movements_telescope(smoke_run):
    cfg, r = smoke_run
    idx = build_plan_index(cfg)
    ev = r.frames["app_db.subscription_events"]
    subs = r.frames["app_db.subscriptions"]
    movement = sum(
        _mrr(idx, t) - _mrr(idx, f) for f, t in zip(ev["from_plan_id"], ev["to_plan_id"])
    )
    final_mrr = sum(
        idx.monthly_price(int(p)) for p in subs.loc[subs.status == "active", "plan_id"].dropna()
    )
    assert abs(movement - final_mrr) < 1e-6


def test_spell_and_event_integrity(smoke_run):
    cfg, r = smoke_run
    subs = r.frames["app_db.subscriptions"]
    ev = r.frames["app_db.subscription_events"]
    assert subs["subscription_id"].is_unique
    assert ev["event_id"].is_unique
    # every event references an existing subscription, and vice versa
    assert set(ev["subscription_id"]) <= set(subs["subscription_id"])
    assert set(subs["subscription_id"]) == set(ev["subscription_id"])
    # canceled spells have a canceled_at; active spells do not
    canceled = subs[subs.status == "canceled"]
    active = subs[subs.status == "active"]
    assert canceled["canceled_at"].notna().all()
    assert active["canceled_at"].isna().all()
    # active/started spells reference a real paid plan
    assert active["plan_id"].notna().all()
    # no empty spells: every spell has a trial_start or a paid start (catches
    # event-aliasing bugs that split a trial spell across subscription ids)
    assert (subs["trial_start_at"].notna() | subs["started_at"].notna()).all()


def test_status_values_valid(smoke_run):
    cfg, r = smoke_run
    subs = r.frames["app_db.subscriptions"]
    assert set(subs["status"]).issubset({"trialing", "active", "canceled"})


def test_trial_conversion_matches_config(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["app_db.subscription_events"]
    n_trials = int((ev["event_type"] == "trial_start").sum())
    n_conv = int((ev["event_type"] == "trial_convert").sum())
    phat = n_conv / n_trials
    p = cfg.lifecycle.trial_convert
    se = (p * (1 - p) / n_trials) ** 0.5
    assert abs(phat - p) < 5 * se, (phat, p)


@pytest.mark.slow
def test_churn_hazard_matches_config():
    # Clean single-plan setup (no upgrade/downgrade/resurrect) so the monthly
    # cancel hazard is directly measurable from converted spells.
    base = load_config("configs/smoke_90d.yaml")
    cfg = base.model_copy(deep=True)
    cfg.timeline.days = 480
    cfg.timeline.end = None
    cfg.plans.plan_mix = {"basic": 1.0}
    cfg.lifecycle.monthly_upgrade = 0.0
    cfg.lifecycle.monthly_downgrade = 0.0
    cfg.lifecycle.monthly_resurrect = 0.0
    cfg.anomalies.scripted = []
    cfg.anomalies.surprise = None

    r = generate(cfg)
    subs = r.frames["app_db.subscriptions"]
    start = cfg.timeline.start
    n_months = int(np.ceil(cfg.timeline.n_days / 30))

    converted = subs[subs["started_at"].notna()].copy()
    start_month = (pd.to_datetime(converted["started_at"]) - pd.Timestamp(start)).dt.days // 30
    canceled = converted["status"] == "canceled"
    end_month = np.where(
        canceled,
        (pd.to_datetime(converted["canceled_at"]) - pd.Timestamp(start)).dt.days // 30,
        n_months - 1,
    )
    exposures = np.maximum(0, end_month - start_month.to_numpy())
    total_exposures = int(exposures.sum())
    total_cancels = int(canceled.sum())
    hazard = total_cancels / total_exposures
    target = cfg.lifecycle.monthly_churn["basic"]
    se = (target * (1 - target) / total_exposures) ** 0.5
    assert abs(hazard - target) < 5 * se, (hazard, target, total_exposures)
