from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def b2b_smoke_config_path() -> Path:
    return REPO_ROOT / "configs" / "smoke_b2b_90d.yaml"


@pytest.fixture(scope="session")
def b2b_cfg(b2b_smoke_config_path):
    from fake_companies.config import load_config

    return load_config(b2b_smoke_config_path)


@pytest.fixture(scope="session")
def b2b_result(b2b_cfg):
    """One shared smoke generation for the whole B2B suite."""
    from fake_companies.generate import generate

    return generate(b2b_cfg)
