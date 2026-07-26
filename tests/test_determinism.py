"""Same seed + config => byte-identical output (the core reproducibility rule)."""

from __future__ import annotations

import hashlib

from fake_companies.config import load_config
from fake_companies.generate import run_generation
from fake_companies.output.export import export_tables
from fake_companies.output.schemas import ALL_TABLES


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_parquet_byte_identical(smoke_config_path, tmp_path):
    cfg = load_config(smoke_config_path)

    db1 = tmp_path / "a.duckdb"
    db2 = tmp_path / "b.duckdb"
    run_generation(cfg, db1, sidecar_dir=tmp_path / "a")
    run_generation(cfg, db2, sidecar_dir=tmp_path / "b")

    ex1 = export_tables(db1, tmp_path / "ex1", fmt="parquet")
    ex2 = export_tables(db2, tmp_path / "ex2", fmt="parquet")

    hashes1 = {p.name: _sha(p) for p in ex1}
    hashes2 = {p.name: _sha(p) for p in ex2}
    assert hashes1 == hashes2, "parquet exports differ between identical runs"
    # sanity: we actually exported every table
    assert len(hashes1) == len(ALL_TABLES)


def test_ground_truth_stable(smoke_config_path):
    from fake_companies.generate import generate

    cfg = load_config(smoke_config_path)
    g1 = generate(cfg)
    g2 = generate(cfg)
    ids1 = [r.id for r in g1.ground_truth]
    ids2 = [r.id for r in g2.ground_truth]
    assert ids1 == ids2
    assert g1.manifest["config_hash"] == g2.manifest["config_hash"]
