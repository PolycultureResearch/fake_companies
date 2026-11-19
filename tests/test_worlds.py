"""World sets (eval-worlds spec 4.3, 4.4, 4.7): build, manifest, stability."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from typer.testing import CliRunner

from fake_companies.cli import app
from fake_companies.config import load_config
from fake_companies.effects import EFFECT_COLUMNS, NOISE_K
from fake_companies.generate import run_generation
from fake_companies.output.export import export_hashes
from fake_companies.worlds import (
    MANIFEST_NAME,
    WorldSetManifest,
    build_world_set,
    load_world_set,
    parse_seeds,
    plan_worlds,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RETAIL = REPO_ROOT / "configs" / "smoke_retail_90d.yaml"
SMOKES = ["smoke_90d", "smoke_retail_90d", "smoke_b2b_90d", "smoke_cpg_90d"]


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def retail_set(tmp_path_factory) -> tuple[Path, WorldSetManifest]:
    """3 seeds x (null + both singles + story), Parquet, built with 2 jobs."""
    out = tmp_path_factory.mktemp("worlds") / "retail"
    manifest = build_world_set(
        RETAIL, out, seeds=[0, 1, 2], null=True, singles="all", story=True, fmt="parquet", jobs=2
    )
    return out, manifest


def test_parse_seeds():
    assert parse_seeds("0-3") == [0, 1, 2, 3]
    assert parse_seeds("7") == [7]
    assert parse_seeds("0-2, 7,1") == [0, 1, 2, 7]
    for bad in ("a", "3-1", "1-", ""):
        with pytest.raises(ValueError, match="seeds"):
            parse_seeds(bad)


def test_plan_orders_worlds_and_singles_imply_the_null():
    plans = plan_worlds("co", ["baseline", "flat"], [0, 1], null=False, singles=["a"], story=True)
    assert [p.id for p in plans[:3]] == [
        "co.baseline.s0.null",
        "co.baseline.s0.only.a",
        "co.baseline.s0.story",
    ]
    assert len(plans) == 12
    single = plans[1]
    assert (single.kind, single.selection, single.twin_of) == ("single", "only:a", plans[0].id)
    assert plan_worlds("co", ["baseline"], [0], null=False, singles=[], story=False) == []


def test_manifest_is_the_contract(retail_set):
    out, manifest = retail_set
    loaded = load_world_set(out)
    assert loaded == manifest == load_world_set(out / MANIFEST_NAME)
    assert loaded.schema_version == 1
    assert loaded.vertical == "retail_dtc" and loaded.format == "parquet"
    assert loaded.config_sha256 == hashlib.sha256(RETAIL.read_bytes()).hexdigest()
    assert loaded.seeds == [0, 1, 2] and loaded.regimes == ["baseline"]

    kinds = [w.kind for w in loaded.worlds]
    assert kinds.count("null") == 3 and kinds.count("single") == 6 and kinds.count("story") == 3
    ids = [w.id for w in loaded.worlds]
    assert len(set(ids)) == len(ids)
    assert "retail_smoke.baseline.s1.only.mobile_checkout_regression" in ids

    for w in loaded.worlds:
        assert (out / w.path).is_dir()
        truth = json.loads((out / w.ground_truth).read_text())
        assert [r["id"] for r in truth] == w.anomalies
        run = json.loads((out / w.run_manifest).read_text())
        assert run["seed"] == str(w.seed) and run["config_hash"] == w.config_hash
        assert run["regime"] == w.regime
        if w.kind == "null":
            assert w.anomalies == [] and w.twin_of is None and w.effects is None
            assert run["anomaly_selection"] == "none"
        elif w.kind == "single":
            assert len(w.anomalies) == 1
            assert loaded.world(w.twin_of).kind == "null"
            assert loaded.world(w.twin_of).seed == w.seed
            assert (out / w.effects).is_file()
        else:
            assert len(w.anomalies) == 2 and w.effects is None
            assert "anomaly_selection" not in run
    assert len(loaded.twins()) == 6


def test_manifest_describes_the_metrics(retail_set):
    _, manifest = retail_set
    by_name = {m.name: m for m in manifest.metrics}
    assert manifest.unsupported_metrics == {}
    assert by_name["conversion_rate"].type == "ratio"
    assert by_name["conversion_rate"].dimensions["channel"] == "session__channel"
    assert by_name["orders"].fill_nulls_with == 0.0
    # Sliceable here, but not by one MetricFlow group-by (two semantic models).
    assert by_name["return_rate"].dimensions == {"category": None}
    assert manifest.noise.k == NOISE_K


def test_parquet_world_holds_the_raw_tables(retail_set):
    out, manifest = retail_set
    world = manifest.world("retail_smoke.baseline.s0.null")
    files = sorted(p.name for p in (out / world.path).iterdir())
    assert files == sorted(
        f"{t}.parquet"
        for t in (
            "ad_platform.ad_spend",
            "web.sessions",
            "shop_db.products",
            "shop_db.customers",
            "shop_db.orders",
            "shop_db.order_items",
            "fulfillment.shipments",
            "shop_db.returns",
            "payments.transactions",
        )
    )
    orders = pd.read_parquet(out / world.path / "shop_db.orders.parquet")
    assert len(orders) > 0 and orders["_loaded_at"].notna().all()


def test_ids_and_bytes_are_stable_across_runs_and_jobs(retail_set, tmp_path):
    """Same config, seed, regime and anomaly -> same id and the same bytes,
    whatever else is in the set and however many processes built it."""
    out, manifest = retail_set
    again = tmp_path / "again"
    rebuilt = build_world_set(
        RETAIL, again, seeds=[0, 1, 2], null=True, singles="all", story=True, fmt="parquet", jobs=1
    )
    assert rebuilt == manifest
    assert _file_hashes(again) == _file_hashes(out)

    # A different selection of worlds: the ones in common are byte-identical.
    subset = tmp_path / "subset"
    small = build_world_set(
        RETAIL, subset, seeds=[1], singles="mobile_checkout_regression", fmt="parquet", jobs=3
    )
    assert [w.id for w in small.worlds] == [
        "retail_smoke.baseline.s1.null",
        "retail_smoke.baseline.s1.only.mobile_checkout_regression",
    ]
    full, part = _file_hashes(out), _file_hashes(subset)
    shared = set(part) - {MANIFEST_NAME, "effects_summary.parquet"}
    assert shared and all(part[name] == full[name] for name in shared)
    assert small.world(small.worlds[1].id) == manifest.world(small.worlds[1].id)


@pytest.mark.parametrize("name", SMOKES)
def test_duckdb_worlds_have_stable_content(name, tmp_path):
    """DuckDB files are compared by content (Parquet export hashes): the
    database file itself is not promised to be byte-stable."""
    config = REPO_ROOT / "configs" / f"{name}.yaml"
    a = build_world_set(config, tmp_path / "a", seeds=[3], null=True, story=True, jobs=1)
    b = build_world_set(config, tmp_path / "b", seeds=[3], null=True, story=True, jobs=1)
    assert a == b and a.format == "duckdb"
    for w in a.worlds:
        ha = export_hashes(tmp_path / "a" / w.path, tmp_path / "ea" / w.kind)
        hb = export_hashes(tmp_path / "b" / w.path, tmp_path / "eb" / w.kind)
        assert ha == hb
    null = a.world(f"{a.company_slug}.baseline.s3.null")
    con = duckdb.connect(str(tmp_path / "a" / null.path), read_only=True)
    assert con.execute("SELECT count(*) FROM meta.ground_truth").fetchone() == (0,)
    con.close()


def test_story_world_is_the_default_generation(retail_set, tmp_path):
    """The story world at the config's own seed is exactly what `generate` writes."""
    cfg = load_config(RETAIL)
    out = tmp_path / "set"
    manifest = build_world_set(RETAIL, out, seeds=[cfg.seed], story=True)
    story = manifest.worlds[0]
    assert story.kind == "story"
    plain = tmp_path / "plain.duckdb"
    run_generation(cfg, plain, sidecar_dir=tmp_path / "plain")
    ours = export_hashes(out / story.path, tmp_path / "e1")
    theirs = export_hashes(plain, tmp_path / "e2")
    ours.pop("meta.run_manifest.parquet")  # the world's manifest also records its regime
    theirs.pop("meta.run_manifest.parquet")
    assert ours == theirs


def test_regimes_and_overrides_are_recorded(tmp_path):
    manifest = build_world_set(
        RETAIL,
        tmp_path,
        seeds=[0],
        regimes=["baseline", "flat"],
        null=True,
        overrides={"noise.ar1": 0.1},
        effects=False,
    )
    base, flat = manifest.worlds
    assert (base.regime, flat.regime) == ("baseline", "flat")
    assert base.overrides == {"noise.ar1": 0.1}
    assert flat.overrides["traffic.channels.*.growth"] == {"kind": "flat"}
    assert flat.overrides["noise.ar1"] == 0.1
    assert base.config_hash != flat.config_hash
    assert base.path == "baseline/s0/null.duckdb" and flat.path == "flat/s0/null.duckdb"
    assert manifest.metrics == [] and manifest.effects_summary is None


def test_bad_requests_are_refused(tmp_path):
    with pytest.raises(ValueError, match="nothing to build"):
        build_world_set(RETAIL, tmp_path, seeds=[0])
    with pytest.raises(ValueError, match="unknown regime"):
        build_world_set(RETAIL, tmp_path, seeds=[0], regimes=["sideways"], null=True)
    with pytest.raises(ValueError, match="unknown scripted anomalies"):
        build_world_set(RETAIL, tmp_path, seeds=[0], singles="nope")
    with pytest.raises(ValueError, match="format"):
        build_world_set(RETAIL, tmp_path, seeds=[0], null=True, fmt="csv")


def test_cli_builds_a_set_quickly(tmp_path):
    """Spec 4.7: 3 seeds x (null + singles) of a smoke config, well under a minute."""
    out = tmp_path / "cli"
    t0 = time.perf_counter()
    result = CliRunner().invoke(
        app,
        ["worlds", "-c", str(RETAIL), "-o", str(out), "--seeds", "0-2", "--null"]
        + ["--singles", "all", "--format", "parquet"],
    )
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert "Built 9 worlds (3 null, 6 single, 0 story)" in result.output
    assert elapsed < 40, f"world set took {elapsed:.1f}s"
    manifest = load_world_set(out)
    effects = pd.read_parquet(out / manifest.twins()[0][0].effects)
    assert list(effects.columns) == EFFECT_COLUMNS
    assert (out / manifest.effects_summary).is_file()


def test_cli_generate_selection_and_set(tmp_path):
    db = tmp_path / "x.duckdb"
    result = CliRunner().invoke(
        app,
        ["generate", "-c", str(RETAIL), "-o", str(db), "--anomalies", "none"]
        + ["--set", "timeline.days=100", "--regime", "no_seasonality"],
    )
    assert result.exit_code == 0, result.output
    assert "0 ground-truth events" in result.output
    run = json.loads((tmp_path / "run_manifest.json").read_text())
    assert run["anomaly_selection"] == "none" and run["regime"] == "no_seasonality"
    assert json.loads(run["overrides"])["timeline.days"] == 100
    bad = CliRunner().invoke(
        app, ["generate", "-c", str(RETAIL), "-o", str(db), "--anomalies", "only:nope"]
    )
    assert bad.exit_code != 0
