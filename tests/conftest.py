from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS = REPO_ROOT / "configs"


@pytest.fixture(scope="session")
def smoke_config_path() -> Path:
    return CONFIGS / "smoke_90d.yaml"


@pytest.fixture(scope="session")
def acme_config_path() -> Path:
    return CONFIGS / "acme_b2c_saas.yaml"


@pytest.fixture
def smoke_cfg(smoke_config_path):
    from fake_companies.config import load_config

    return load_config(smoke_config_path)
