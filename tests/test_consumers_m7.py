"""M7 consumer-integration gate (requires the dbt extra + mf binary).

Generates a small database, runs `dbt build`, then verifies both consumer
contracts via scripts/verify_consumers.py: every Breakdown tree metric returns a
daily MetricFlow series, and every Tremor dataflow relation exposes the required
columns. Skipped when the dbt extra isn't installed (the default test env).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MF = ROOT / ".venv" / "bin" / "mf"
DBT = ROOT / ".venv" / "bin" / "dbt"

_available = importlib.util.find_spec("dbt") is not None and MF.exists() and DBT.exists()


@pytest.mark.slow
@pytest.mark.skipif(not _available, reason="dbt extra / mf not installed")
def test_consumer_contracts(tmp_path):
    db = tmp_path / "consumers.duckdb"
    base_env = {**os.environ, "PYTHONPATH": "src"}

    gen = subprocess.run(
        [
            sys.executable,
            "-m",
            "fake_companies.cli",
            "generate",
            "--config",
            "configs/smoke_90d.yaml",
            "--out",
            str(db),
        ],
        cwd=ROOT,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert gen.returncode == 0, gen.stderr

    env = {**os.environ, "DBT_PROFILES_DIR": str(ROOT / "dbt"), "FAKE_DB": str(db)}
    build = subprocess.run(
        [str(DBT), "build", "--project-dir", "dbt"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-2000:]

    verify = subprocess.run(
        [sys.executable, "scripts/verify_consumers.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "ALL CONSUMER CONTRACTS OK" in verify.stdout
