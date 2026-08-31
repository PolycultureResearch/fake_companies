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


def test_product_events_present(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["product.events"]
    assert len(ev) > 0
    assert ev["event_id"].is_unique


def test_intraday_shape(smoke_run):
    cfg, r = smoke_run
    hours = pd.to_datetime(r.frames["product.events"]["occurred_at"]).dt.hour
    night = ((hours >= 0) & (hours <= 5)).mean()
    day = ((hours >= 12) & (hours <= 21)).mean()
    assert night < 0.10  # quiet overnight
    assert day > 0.45  # busy daytime/evening


def test_weekend_uplift(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["product.events"]
    dow = pd.to_datetime(ev["occurred_at"]).dt.dayofweek
    per_weekday = (dow < 5).sum() / 5
    per_weekend = (dow >= 5).sum() / 2
    # configured weekend_uplift > 1 => more events per weekend day
    assert per_weekend > per_weekday


def _event_days(ev, cal):
    """(user_id, day_index) of every event, with its plan."""
    day = (pd.to_datetime(ev["occurred_at"]).dt.normalize() - pd.Timestamp(cal.start)).dt.days
    return pd.DataFrame({"plan": ev["plan_at_event"], "u": ev["user_id"], "d": day})


def test_dau_over_active_is_the_realized_active_share(smoke_run):
    """``dau_over_active`` is a probability, not an event-rate factor.

    Measured on the cleanest cohort available — users who never trialed and
    never subscribed, so their free segment is exactly ``[created_day, end)``.
    The realized share of active days must land on the configured knob (times
    the weekend uplift), not several multiples above it: the module used to
    fold the knob into an NHPP intensity, which put the realized share at
    ``1 - exp(-dau * events_per_active_day)`` — 0.38 here against a configured
    0.08. The band is wide enough for sampling noise and narrow enough that
    that construction cannot pass.
    """
    cfg, r = smoke_run
    users, subs, cal = r.frames["app_db.users"], r.frames["app_db.subscriptions"], r.calendar
    ever_paid = set(subs.loc[subs["started_at"].notna(), "user_id"])
    elig = users[~users["started_trial"].astype(bool) & ~users["user_id"].isin(ever_paid)]
    user_days = int((cal.n_days - elig["day_index"]).clip(lower=0).sum())
    assert user_days > 10_000  # enough mass for the tolerance below to mean something

    ed = _event_days(r.frames["product.events"], cal)
    active = ed[(ed["plan"] == "free") & ed["u"].isin(set(elig["user_id"]))]
    share = len(active.drop_duplicates(["u", "d"])) / user_days

    weekend_mean = (5 + 2 * cfg.engagement.weekend_uplift) / 7
    expected = cfg.engagement.dau_over_active["free"] * weekend_mean
    assert 0.75 * expected < share < 1.35 * expected, (share, expected)


def test_events_per_active_day_is_the_conditional_mean(smoke_run):
    """Events are drawn *conditional on the day being active*, so the mean over
    active user-days is the configured ``events_per_active_day`` — the same
    ``1 + Poisson(epd - 1)`` construction the trial window uses."""
    cfg, r = smoke_run
    ed = _event_days(r.frames["product.events"], r.calendar)
    per_active = (
        ed.groupby("plan").size() / ed.drop_duplicates(["plan", "u", "d"]).groupby("plan").size()
    )
    for plan, configured in cfg.engagement.events_per_active_day.items():
        assert abs(per_active[plan] / configured - 1) < 0.15, (plan, per_active[plan], configured)


def test_plan_at_event_values(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["product.events"]
    assert set(ev["plan_at_event"]).issubset({"free", "basic", "pro"})
    # paid engagement should be present
    assert (ev["plan_at_event"] != "free").any()


def test_events_reference_real_users(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["product.events"]
    users = set(r.frames["app_db.users"]["user_id"])
    assert set(ev["user_id"].unique()) <= users


def test_feature_mix_roughly_matches(smoke_run):
    cfg, r = smoke_run
    ev = r.frames["product.events"]
    counts = ev["event_name"].value_counts(normalize=True)
    for feat, p in cfg.engagement.feature_mix.items():
        assert abs(counts.get(feat, 0.0) - p) < 0.03, (feat, counts.get(feat, 0.0), p)


def test_determinism(smoke_run):
    cfg, r = smoke_run
    r2 = generate(cfg)
    a = r.frames["product.events"]
    b = r2.frames["product.events"]
    assert len(a) == len(b)
    assert np.array_equal(a["user_id"].to_numpy(), b["user_id"].to_numpy())
    assert (a["occurred_at"].to_numpy() == b["occurred_at"].to_numpy()).all()
