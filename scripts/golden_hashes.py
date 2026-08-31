"""Pin per-table parquet hashes for the B2C SaaS scenarios.

Safety net for the vertical-split migration: every refactor PR must leave the
raw tables and meta.ground_truth byte-identical for the existing configs.
meta.run_manifest is excluded — it carries config_hash and generation metadata
that legitimately change across the migration.

Usage: uv run python scripts/golden_hashes.py
Rewrites tests/golden/b2c_hashes.json. Regenerate only on an intentional
behavior change, never to make a migration PR pass.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fake_companies.config import load_config
from fake_companies.generate import run_generation
from fake_companies.output.export import export_hashes

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "b2c_hashes.json"
CONFIG_NAMES = ["acme_b2c_saas", "white_cube_b2c_app", "smoke_90d"]
EXCLUDED = {"meta.run_manifest.parquet"}


def compute_hashes(config_name: str) -> dict[str, str]:
    cfg = load_config(REPO_ROOT / "configs" / f"{config_name}.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "out.duckdb"
        run_generation(cfg, db, sidecar_dir=tmp)
        hashes = export_hashes(db, Path(tmp) / "export")
    return {name: sha for name, sha in sorted(hashes.items()) if name not in EXCLUDED}


def main() -> None:
    golden = {name: compute_hashes(name) for name in CONFIG_NAMES}
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(golden, indent=2) + "\n")
    print(f"Wrote {GOLDEN_PATH} ({sum(len(v) for v in golden.values())} table hashes)")


if __name__ == "__main__":
    main()
