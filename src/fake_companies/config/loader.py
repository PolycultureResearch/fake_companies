"""Load and hash scenario YAML into a validated :class:`ScenarioConfig`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from .schema import ScenarioConfig


def load_config(path: str | Path) -> ScenarioConfig:
    """Parse and validate a scenario YAML file."""
    raw = Path(path).read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise TypeError(f"config {path} did not parse to a mapping")
    return ScenarioConfig.model_validate(data)


def config_hash(cfg: ScenarioConfig) -> str:
    """Stable content hash of a config (for the run manifest / reproducibility)."""
    payload = cfg.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
