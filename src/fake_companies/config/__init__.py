"""Scenario configuration: pydantic envelope + shared sections + YAML loader."""

from __future__ import annotations

from .loader import config_hash, load_config
from .schema import BaseScenarioConfig

__all__ = ["BaseScenarioConfig", "ScenarioConfig", "config_hash", "load_config"]


def __getattr__(name: str):
    # Legacy alias kept through the vertical-split migration: the pre-split
    # ScenarioConfig is the B2C SaaS config model. Lazy so config <-> verticals
    # stays import-time acyclic.
    if name == "ScenarioConfig":
        from ..verticals.b2c_saas.config import B2CSaaSScenarioConfig

        return B2CSaaSScenarioConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
