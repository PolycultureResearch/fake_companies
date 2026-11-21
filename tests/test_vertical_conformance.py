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
    "cpg_wholesale": "smoke_cpg_90d.yaml",
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


@pytest.mark.parametrize("name", VERTICALS)
def test_every_driver_has_a_root_metric(name, smoke_cfgs):
    """Every driver names a root metric the dbt project defines, and a slice
    of it MetricFlow can address (eval-worlds spec 4.6)."""
    from fake_companies.semantic import SemanticLayer

    v = REGISTRY[name]
    layer = SemanticLayer.for_vertical(v)
    for driver in sorted(v.known_drivers(smoke_cfgs[name])):
        label = v.root_label(driver)
        assert label.metric in layer.metrics, f"driver {driver!r}: unknown root {label.metric!r}"
        addressable = layer.dimensions(label.metric)
        for dim in label.slice:
            assert addressable.get(dim), (
                f"driver {driver!r}: root {label.metric!r} has no MetricFlow dimension {dim!r}"
            )
        if label.rate is not None:
            assert label.rate in layer.metrics, (driver, label.rate)
            assert layer.metrics[label.rate].kind == "ratio"
            assert label.metric == layer.metrics[label.rate].inputs["num"], (driver, label)
        if "." in driver:
            member = driver.split(".", 1)[1]
            assert member in label.slice.values() or not label.slice, (driver, label)


@pytest.mark.parametrize(
    "config", sorted((REPO_ROOT / "configs").glob("*.yaml")), ids=lambda p: p.stem
)
def test_scripted_anomalies_have_addressable_roots(config):
    """Every scripted rate anomaly in every shipped config gets a root label
    whose whole slice (driver member + segment) the root metric can be cut by."""
    from fake_companies.semantic import SemanticLayer

    cfg = load_config(config)
    v = REGISTRY[cfg.company.vertical]
    layer = SemanticLayer.for_vertical(v)
    for a in cfg.anomalies.scripted:
        if a.kind != "rate":
            continue
        label = v.root_label(a.target, a.segment)
        addressable = layer.dimensions(label.metric)
        assert all(addressable.get(d) for d in label.slice), (a.name, label)
        assert (label.dimension is None) == (label.value is None)


def test_root_label_rules():
    from fake_companies.verticals.base import RootLabel

    retail = REGISTRY["retail_dtc"]
    assert retail.root_label("spend.paid_social") == RootLabel(
        "marketing_spend", {"channel": "paid_social"}
    )
    label = retail.root_label("conversion_rate.paid_search", {"device": "mobile"})
    assert (label.metric, label.dimension, label.value) == (
        "conversion_rate",
        "channel,device",
        "paid_search,mobile",
    )
    # A segment on a driver whose builder ignores segments adds nothing.
    assert retail.root_label("spend.paid_social", {"device": "mobile"}).slice == {
        "channel": "paid_social"
    }
    assert retail.root_label("items_per_order") == RootLabel("units", {})
    assert retail.root_label("items_per_order").dimension is None
    cpg = REGISTRY["cpg_wholesale"]
    assert cpg.root_label("consumer_demand.teas", {"banner": "whole_earth"}).slice == {
        "category": "teas",
        "retailer_banner": "whole_earth",
    }
    with pytest.raises(KeyError):
        retail.root_label("not_a_driver")
    # A root that is a rate's numerator count also names the rate.
    assert retail.root_label("return_rate.jackets") == RootLabel(
        "returned_units", {"category": "jackets"}, "return_rate"
    )
    assert REGISTRY["b2c_saas"].root_label("churn.pro").rate == "customer_churn_rate"
    assert retail.root_label("spend.paid_social").rate is None


@pytest.mark.parametrize("name", VERTICALS)
def test_stock_metrics_exist(name):
    from fake_companies.semantic import SemanticLayer

    v = REGISTRY[name]
    layer = SemanticLayer.for_vertical(v)
    assert v.stock_metrics() <= set(layer.metrics)
    for metric in v.stock_metrics():
        assert layer.time_aggregation(metric, v.stock_metrics()) == "end_of_period"


def test_declared_effects_match_what_was_measured():
    """Labels corrected from the measured effects (eval-worlds spec, section 9).

    - A category mix shift cannot move total units or orders; it moves what
      depends on the price and margin of the mix.
    - A slower sales cycle delays every stage exit, so wins, bookings and
      invoicing dip while open deals pile up.
    """
    retail = REGISTRY["retail_dtc"].affected_metrics("category_demand.tees")
    assert "units" not in retail and "orders" not in retail
    assert {"gross_revenue", "net_revenue", "gross_margin", "aov", "margin_rate"} == set(retail)
    b2b = REGISTRY["b2b_services"].affected_metrics("cycle_time_scale")
    assert {"bookings", "deals_won", "negotiations", "proposals_sent", "invoiced_revenue"} <= set(
        b2b
    )
    assert {"sales_cycle_days", "open_deals", "open_pipeline_value"} <= set(b2b)


def test_new_metrics_are_declared_where_they_move():
    """customer_base is the running total of new_customers; trade_inventory_build
    is shipments less POS; discounted_orders exists only inside a promotion."""
    import datetime as dt

    from fake_companies.verticals.retail_dtc.metrics import affected_metrics_in_window

    retail = REGISTRY["retail_dtc"]
    for driver in ("spend.paid_social", "sessions.organic", "conversion_rate.paid_search"):
        declared = retail.affected_metrics(driver)
        assert "new_customers" in declared and "customer_base" in declared, driver
    for driver in ("repeat_rate", "items_per_order", "category_demand.tees", "return_rate.tees"):
        assert "customer_base" not in retail.affected_metrics(driver), driver
    cpg = REGISTRY["cpg_wholesale"]
    for driver in ("consumer_demand.teas", "velocity.whole_earth"):
        assert "trade_inventory_build" in cpg.affected_metrics(driver)
    assert cpg.affected_metrics("spend.paid_social") == ["marketing_spend"]  # still decoupled

    cfg = load_config(REPO_ROOT / "configs" / "alpenglow_retail_dtc.yaml")
    promo = cfg.promos[1]  # Black Friday 2024
    inside = (promo.window.start, promo.window.end)
    outside = (dt.date(2024, 9, 12), dt.date(2024, 9, 23))
    for driver in ("conversion_rate.paid_search", "spend.paid_social", "repeat_rate"):
        assert "discounted_orders" in affected_metrics_in_window(cfg, driver, *inside)
        assert "discounted_orders" not in affected_metrics_in_window(cfg, driver, *outside)
    # an open-ended window reaches every later promotion
    assert "discounted_orders" in affected_metrics_in_window(
        cfg, "sessions.organic", dt.date(2024, 9, 1), None
    )
    # drivers that cannot change how many orders there are never declare it
    for driver in ("items_per_order", "category_demand.tees", "return_rate.tees"):
        assert "discounted_orders" not in affected_metrics_in_window(cfg, driver, *inside)
