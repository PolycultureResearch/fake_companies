from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def retail_smoke_config_path() -> Path:
    return REPO_ROOT / "configs" / "smoke_retail_90d.yaml"


@pytest.fixture(scope="session")
def retail_cfg(retail_smoke_config_path):
    from fake_companies.config import load_config

    return load_config(retail_smoke_config_path)


@pytest.fixture(scope="session")
def retail_result(retail_cfg):
    """One shared smoke generation for the whole retail suite."""
    from fake_companies.generate import generate

    return generate(retail_cfg)
