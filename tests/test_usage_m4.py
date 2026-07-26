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
