from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from fake_companies.config import config_hash, load_config
from fake_companies.verticals.b2c_saas.config import B2CSaaSScenarioConfig


def test_canonical_config_loads(reference_config_path):
    cfg = load_config(reference_config_path)
    assert cfg.company.slug == "b2c_reference"
    assert cfg.timeline.n_days == 730
    assert cfg.timeline.end_date == dt.date(2025, 12, 30)
    assert len(cfg.weekly_shape) == 7
    assert cfg.anomalies.scripted  # scripted events present
    assert cfg.anomalies.surprise is not None


def test_smoke_config_loads(smoke_config_path):
    cfg = load_config(smoke_config_path)
    assert cfg.timeline.n_days == 90


def test_default_vertical_is_b2c_saas(smoke_config_path):
    cfg = load_config(smoke_config_path)
    assert cfg.company.vertical == "b2c_saas"
    assert isinstance(cfg, B2CSaaSScenarioConfig)


def test_unknown_vertical_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("company:\n  name: X\n  slug: x\n  vertical: hedge_fund\n")
    with pytest.raises(ValueError, match="unknown vertical 'hedge_fund'"):
        load_config(bad)


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        B2CSaaSScenarioConfig.model_validate(
            {
                "company": {"name": "X", "slug": "x"},
                "timeline": {"start": "2024-01-01", "days": 10},
                "traffic": {"channels": {"organic": {"kind": "fixed", "baseline": 1}}},
                "mix": {"country": {"US": 1.0}, "device": {"desktop": 1.0}},
                "funnel": {"signup_rate": {}},
                "plans": {"plans": [], "plan_mix": {}},
                "lifecycle": {"monthly_churn": {}},
                "engagement": {
                    "dau_over_active": {},
                    "events_per_active_day": {},
                    "feature_mix": {},
                },
                "loading": {"sources": {}},
                "bogus_key": 1,
            }
        )


def test_anomaly_window_outside_timeline_rejected(smoke_config_path):
    cfg = load_config(smoke_config_path)
    data = cfg.model_dump(mode="json", by_alias=True)
    data["anomalies"]["scripted"][0]["window"]["start"] = "2030-01-01"
    with pytest.raises(ValidationError):
        B2CSaaSScenarioConfig.model_validate(data)


def test_bad_segment_dim_rejected(smoke_config_path):
    # Segment dims are vertical-owned: "platform" is not a b2c_saas dim, so
    # validation fails on the full scenario config, not on ScriptedAnomaly.
    cfg = load_config(smoke_config_path)
    data = cfg.model_dump(mode="json", by_alias=True)
    data["anomalies"]["scripted"][0]["segment"] = {"platform": "ios"}
    with pytest.raises(ValidationError, match="segment dims"):
        B2CSaaSScenarioConfig.model_validate(data)


def test_config_hash_stable(smoke_config_path):
    cfg1 = load_config(smoke_config_path)
    cfg2 = load_config(smoke_config_path)
    assert config_hash(cfg1) == config_hash(cfg2)
