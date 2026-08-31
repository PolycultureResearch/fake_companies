from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def cpg_smoke_config_path() -> Path:
    return REPO_ROOT / "configs" / "smoke_cpg_90d.yaml"


@pytest.fixture(scope="session")
def cpg_cfg(cpg_smoke_config_path):
    from fake_companies.config import load_config

    return load_config(cpg_smoke_config_path)


@pytest.fixture(scope="session")
def cpg_result(cpg_cfg):
    """One shared smoke generation for the whole CPG suite."""
    from fake_companies.generate import generate

    return generate(cpg_cfg)
