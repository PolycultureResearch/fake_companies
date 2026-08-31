#!/usr/bin/env python
"""Build the four demo datasets: generate raw data + materialize dbt marts.

Produces, per company, a self-contained bundle under ``out/datasets/<slug>/``:

    <slug>.duckdb             raw tables + dbt staging/marts/semantic materialized
    <slug>.ground_truth.json  the planted-anomaly scoring key
    <slug>.run_manifest.json  seed, config hash, timeline, vertical

(Sidecars are slug-prefixed because GitHub release assets share one flat
namespace across all four bundles.)

These are the artifacts published as a GitHub release for consumers (the
Breakdown demos and their agents) to download instead of regenerating:

    uv run python scripts/build_demo_datasets.py
    gh release create datasets-vN out/datasets/*/* --title "Demo datasets vN" \
        --notes "fake_companies $(git rev-parse --short HEAD)"

Generation is seed-deterministic and cross-platform byte-identical, so the
release is reproducible from the config + commit named in its notes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from fake_companies.config import load_config
from fake_companies.generate import run_generation

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT = REPO_ROOT / ".venv" / "bin" / "dbt"

# (config name, company slug) — the vertical/dbt project comes from the config.
DEMOS = [
    ("white_cube_b2c_app", "white_cube"),
    ("alpenglow_retail_dtc", "alpenglow"),
    ("meridian_b2b_services", "meridian"),
    ("bristlecone_cpg", "bristlecone"),
]


def build(config_name: str, slug: str) -> Path:
    cfg = load_config(REPO_ROOT / "configs" / f"{config_name}.yaml")
    out_dir = REPO_ROOT / "out" / "datasets" / slug
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    db = out_dir / f"{slug}.duckdb"

    result = run_generation(cfg, db, sidecar_dir=out_dir)
    (out_dir / "ground_truth.json").rename(out_dir / f"{slug}.ground_truth.json")
    (out_dir / "run_manifest.json").rename(out_dir / f"{slug}.run_manifest.json")
    vertical = result.manifest["vertical"]
    print(f"  generated {db.name}: {sum(len(f) for f in result.frames.values())} rows")

    dbt_dir = REPO_ROOT / "dbt" / vertical
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir), "FAKE_DB": str(db)}
    proc = subprocess.run(
        [str(DBT), "build", "--project-dir", str(dbt_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"dbt build failed for {slug}")
    print(f"  dbt marts built into {db.name} ({db.stat().st_size / 1e6:.0f} MB)")
    return db


def main() -> None:
    if not DBT.exists():
        raise SystemExit("dbt not installed — run `uv sync --extra dbt` first")
    for config_name, slug in DEMOS:
        print(f"== {slug} ({config_name})")
        build(config_name, slug)
    print("done: out/datasets/")


if __name__ == "__main__":
    main()
