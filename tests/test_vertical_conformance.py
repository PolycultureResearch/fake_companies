"""Vertical conformance: every registered vertical is internally consistent.

The vertical owns what used to be four hand-synced registries (tables, driver
catalog, dq targets, driver->metric map) plus a dbt project. These checks make
the sync failures that used to be possible loud: a driver the entities read
but build_drivers doesn't emit, a dq target column that doesn't exist, an
affected_metrics name the dbt semantic layer doesn't define.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fake_companies.config import BaseScenarioConfig, load_config
from fake_companies.core import RngHub, build_calendar
from fake_companies.verticals import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every vertical must ship a smoke config (CI scale) named here.
SMOKE_CONFIGS = {
    "b2c_saas": "smoke_90d.yaml",
    "retail_dtc": "smoke_retail_90d.yaml",
    "b2b_services": "smoke_b2b_90d.yaml",
}

VERTICALS = sorted(REGISTRY)


@pytest.fixture(scope="module")
def smoke_cfgs():
    return {name: load_config(REPO_ROOT / "configs" / SMOKE_CONFIGS[name]) for name in VERTICALS}


def test_every_vertical_has_a_smoke_config():
    assert set(SMOKE_CONFIGS) == set(REGISTRY)


@pytest.mark.parametrize("name", VERTICALS)
def test_config_model_and_segment_dims(name, smoke_cfgs):
    v = REGISTRY[name]
    model = v.config_model()
    assert issubclass(model, BaseScenarioConfig)
    assert isinstance(smoke_cfgs[name], model)
    assert v.segment_dims(), "segment_dims must be non-empty"
    assert v.dbt_project(), "dbt_project must name a directory"
    assert (REPO_ROOT / "dbt" / v.dbt_project() / "dbt_project.yml").exists()


@pytest.mark.parametrize("name", VERTICALS)
def test_table_specs_consistent(name):
    v = REGISTRY[name]
    tables = v.tables()
    fqns = [t.fqn for t in tables]
    assert len(fqns) == len(set(fqns)), "duplicate table fqns"
    for spec in tables:
        assert spec.loaded_at == "_loaded_at"
        assert "_loaded_at" in spec.columns
        if spec.event_time:
            assert spec.event_time in spec.columns, (spec.fqn, spec.event_time)
        ref = spec.loading_ref
        for col in (ref,) if isinstance(ref, str) else (ref or ()):
            assert col in spec.columns, (spec.fqn, col)


@pytest.mark.parametrize("name", VERTICALS)
def test_dq_targets_reference_real_columns(name):
    v = REGISTRY[name]
    by_fqn = {t.fqn: t for t in v.tables()}
    targets = v.dq_targets()
    assert set(targets) <= set(by_fqn), "dq target not in tables()"
    for fqn, meta in targets.items():
        cols = set(by_fqn[fqn].columns)
        for klass in ("categorical", "nullable", "numeric"):
            missing = set(meta.get(klass, [])) - cols
            assert not missing, (fqn, klass, missing)
    assert set(v.dq_surprise_tables()) <= set(targets)


@pytest.mark.parametrize("name", VERTICALS)
def test_drivers_and_frames_agree(name, smoke_cfgs):
    """build_drivers emits exactly known_drivers; entities fill exactly tables()."""
    v = REGISTRY[name]
    cfg = smoke_cfgs[name]
    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed)

    panel = v.build_drivers(cfg, cal, rng)
    assert set(panel.names) == v.known_drivers(cfg)

    frames: dict = {}
    v.build_entities(cfg, cal, rng, panel, frames)
    fqns = {t.fqn for t in v.tables()}
    assert set(frames) == fqns
    for spec in v.tables():
        # Every spec column except the loading stamp must come from the entity
        # layer (_loaded_at is filled by the corruption/loading stage).
        missing = set(spec.columns) - set(frames[spec.fqn].columns) - {"_loaded_at"}
        assert not missing, (spec.fqn, missing)


@pytest.mark.parametrize("name", VERTICALS)
def test_affected_metrics_exist_in_dbt(name, smoke_cfgs):
    v = REGISTRY[name]
    metrics_yml = REPO_ROOT / "dbt" / v.dbt_project() / "models" / "semantic" / "metrics.yml"
    defined = {m["name"] for m in yaml.safe_load(metrics_yml.read_text())["metrics"]}

    for driver in sorted(v.known_drivers(smoke_cfgs[name])):
        affected = v.affected_metrics(driver)
        assert affected, f"driver {driver!r} maps to no metrics"
        unknown = set(affected) - defined
        assert not unknown, f"driver {driver!r} names metrics not in {metrics_yml.name}: {unknown}"
