from __future__ import annotations

import duckdb

from fake_companies.config import load_config
from fake_companies.generate import generate, run_generation
from fake_companies.output.schemas import ALL_TABLES


def test_generate_writes_all_tables(smoke_config_path, tmp_path):
    cfg = load_config(smoke_config_path)
    out = tmp_path / "smoke.duckdb"
    run_generation(cfg, out)
    assert out.exists()

    con = duckdb.connect(str(out), read_only=True)
    try:
        for spec in ALL_TABLES:
            n = con.execute(f"SELECT count(*) FROM {spec.fqn}").fetchone()[0]
            assert n >= 0  # table exists and is queryable
    finally:
        con.close()


def test_manifest_reproducible(smoke_config_path, tmp_path):
    cfg = load_config(smoke_config_path)
    r1 = generate(cfg)
    r2 = generate(cfg)
    assert r1.manifest == r2.manifest
    assert r1.manifest["config_hash"]
    assert r1.manifest["seed"] == str(cfg.seed)


def test_sidecars_written(smoke_config_path, tmp_path):
    cfg = load_config(smoke_config_path)
    out = tmp_path / "smoke.duckdb"
    run_generation(cfg, out, sidecar_dir=tmp_path)
    assert (tmp_path / "run_manifest.json").exists()
    assert (tmp_path / "ground_truth.json").exists()


def test_seed_override_changes_manifest(smoke_config_path):
    cfg = load_config(smoke_config_path)
    base = generate(cfg).manifest
    other = generate(cfg, seed=cfg.seed + 1).manifest
    assert base["seed"] != other["seed"]
