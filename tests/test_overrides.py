"""Config overrides and named regimes (eval-worlds spec 4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from fake_companies.config import config_hash, load_config
from fake_companies.config.overrides import (
    apply_overrides,
    load_regimes,
    parse_override,
    parse_overrides,
)
from fake_companies.generate import generate
from fake_companies.verticals import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted(p for p in (REPO_ROOT / "configs").glob("*.yaml"))
REGIMES = ["flat", "declining", "accelerating", "long_history", "no_seasonality"]


def test_parse_override_reads_yaml_values():
    assert parse_override("timeline.days=1095") == ("timeline.days", 1095)
    assert parse_override("calendar.annual_amp=0") == ("calendar.annual_amp", 0)
    assert parse_override("a.b={kind: flat}") == ("a.b", {"kind": "flat"})
    assert parse_override("timeline.end=null") == ("timeline.end", None)
    assert parse_overrides(None) == {}
    with pytest.raises(ValueError, match="dotted.path=value"):
        parse_override("timeline.days")


def test_apply_overrides_paths():
    data = {"a": {"x": {"g": 1}, "y": {"g": 2}}, "l": [{"v": 1}, {"v": 2}]}
    out = apply_overrides(data, {"a.*.g": 9, "l.1.v": 7, "a.x.new": True})
    assert out == {"a": {"x": {"g": 9, "new": True}, "y": {"g": 9}}, "l": [{"v": 1}, {"v": 7}]}
    assert data["a"]["x"] == {"g": 1}, "the input mapping is not mutated"
    for bad in ({"nope.x": 1}, {"l.5.v": 1}, {"a.x.g.deeper": 1}, {"l.*.v.*": 1}):
        with pytest.raises(ValueError, match="override"):
            apply_overrides(data, bad)
    with pytest.raises(ValueError, match="matched nothing"):
        apply_overrides({"a": {}}, {"a.*": 1})


def test_overrides_are_validated_like_the_file(smoke_config_path):
    with pytest.raises(ValidationError):  # unknown key: still a load-time error
        load_config(smoke_config_path, overrides={"calendar.anual_amp": 0})
    with pytest.raises(ValidationError):  # cuts a scripted anomaly out of the timeline
        load_config(smoke_config_path, overrides={"timeline.days": 30})
    with pytest.raises(ValueError, match="company.vertical"):
        load_config(smoke_config_path, overrides={"company.vertical": "retail_dtc"})


def test_overrides_change_the_config_and_are_recorded(smoke_config_path):
    base = load_config(smoke_config_path)
    cfg = load_config(smoke_config_path, overrides={"timeline.days": 120, "noise.ar1": 0.1})
    assert cfg.timeline.n_days == 120 and cfg.noise.ar1 == 0.1
    assert cfg.applied_overrides == {"timeline.days": 120, "noise.ar1": 0.1}
    assert base.applied_overrides == {} and base.regime is None
    assert config_hash(cfg) != config_hash(base)

    manifest = generate(cfg, anomalies="none").manifest  # selection keeps the provenance
    assert yaml.safe_load(manifest["overrides"]) == {"timeline.days": 120, "noise.ar1": 0.1}
    assert manifest["n_days"] == "120"


def test_no_overrides_is_the_plain_load(smoke_config_path):
    plain = load_config(smoke_config_path)
    assert load_config(smoke_config_path, overrides={}, regime=None) == plain
    baseline = load_config(smoke_config_path, regime="baseline")
    assert baseline.model_dump() == plain.model_dump()
    assert config_hash(baseline) == config_hash(plain)
    assert baseline.regime == "baseline" and baseline.applied_overrides == {}


@pytest.mark.parametrize("vertical", sorted(REGISTRY))
def test_every_vertical_names_the_five_regimes(vertical):
    regimes = load_regimes(vertical)
    assert set(regimes) == {"baseline", *REGIMES}
    assert regimes["baseline"] == {}
    for name, overrides in regimes.items():
        assert not any(p.startswith("anomalies") for p in overrides), name


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.stem)
@pytest.mark.parametrize("regime", REGIMES)
def test_every_regime_loads_on_every_config(config, regime):
    base = load_config(config)
    cfg = load_config(config, regime=regime)
    assert cfg.regime == regime and cfg.applied_overrides
    assert cfg.anomalies == base.anomalies, "a regime must not add or remove anomalies"
    if regime == "long_history":
        assert cfg.timeline.n_days == 1095 and cfg.timeline.start == base.timeline.start
    if regime == "no_seasonality":
        assert cfg.calendar.annual_amp == 0 and set(cfg.weekly_shape) == {1.0}


def test_regime_may_not_touch_anomalies(tmp_path):
    (tmp_path / "b2c_saas.yaml").write_text("sneaky:\n  anomalies.scripted: []\n")
    with pytest.raises(ValueError, match="may not touch anomalies"):
        load_regimes("b2c_saas", tmp_path)


def test_unknown_regime_is_refused(smoke_config_path):
    with pytest.raises(ValueError, match="unknown regime"):
        load_config(smoke_config_path, regime="sideways")


def test_flat_regime_flattens_the_drivers():
    path = REPO_ROOT / "configs" / "smoke_retail_90d.yaml"
    base = generate(load_config(path), anomalies="none")
    flat = generate(load_config(path, regime="flat"), anomalies="none")

    # organic grows 0.3%/day in the smoke config: flat must end the run lower.
    def late_organic(result):
        s = result.frames["web.sessions"]
        return int(((s["channel"] == "organic") & (s["day_index"] >= 60)).sum())

    assert late_organic(flat) < 0.9 * late_organic(base)
    assert flat.manifest["regime"] == "flat"
