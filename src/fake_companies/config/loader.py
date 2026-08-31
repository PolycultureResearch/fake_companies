"""Load and hash scenario YAML into a validated scenario config.

Loading is two-stage: peek ``company.vertical`` from the raw mapping (default
``b2c_saas``), resolve the vertical from the registry, then validate the whole
document against that vertical's config model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import BaseScenarioConfig


def load_config(path: str | Path) -> BaseScenarioConfig:
    """Parse and validate a scenario YAML file against its vertical's model."""
    raw = Path(path).read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise TypeError(f"config {path} did not parse to a mapping")

    company = data.get("company")
    vertical_name = "b2c_saas"
    if isinstance(company, dict) and company.get("vertical") is not None:
        vertical_name = str(company["vertical"])

    from ..verticals import get_vertical

    try:
        vertical = get_vertical(vertical_name)
    except KeyError as e:
        raise ValueError(f"config {path}: {e.args[0]}") from None

    try:
        return vertical.config_model().model_validate(data)
    except ValidationError as e:
        e.add_note(f"while validating {path} as vertical {vertical.name!r}")
        raise


def config_hash(cfg: BaseScenarioConfig) -> str:
    """Stable content hash of a config (for the run manifest / reproducibility)."""
    payload = cfg.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
