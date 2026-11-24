"""Load and hash scenario YAML into a validated scenario config.

Loading is two-stage: peek ``company.vertical`` from the raw mapping (default
``b2c_saas``), resolve the vertical from the registry, then validate the whole
document against that vertical's config model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import BaseScenarioConfig


def load_config(
    path: str | Path,
    overrides: dict[str, Any] | None = None,
    regime: str | None = None,
) -> BaseScenarioConfig:
    """Parse and validate a scenario YAML file against its vertical's model.

    ``regime`` names a set of overrides from ``configs/regimes/<vertical>.yaml``
    and ``overrides`` maps dotted paths to values (see ``config.overrides``);
    the regime is applied first, then the explicit overrides. Both happen
    before validation, and both are remembered on the returned config
    (``applied_overrides`` / ``regime``) so the run manifest can record them.
    With neither, this is the plain load it always was.
    """
    raw = Path(path).read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise TypeError(f"config {path} did not parse to a mapping")

    applied: dict[str, Any] = {}
    if regime is not None or overrides:
        from .overrides import BASELINE_REGIME, apply_overrides, regime_overrides

        peek = data.get("company")
        peek_vertical = "b2c_saas"
        if isinstance(peek, dict) and peek.get("vertical") is not None:
            peek_vertical = str(peek["vertical"])
        if regime is not None and regime != BASELINE_REGIME:
            applied.update(regime_overrides(peek_vertical, regime))
        applied.update(overrides or {})
        data = apply_overrides(data, applied)
        if not isinstance(data.get("company"), dict) or (
            str(data["company"].get("vertical", "b2c_saas")) != peek_vertical
        ):
            raise ValueError(f"config {path}: an override may not change company.vertical")

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
        cfg = vertical.config_model().model_validate(data)
    except ValidationError as e:
        e.add_note(f"while validating {path} as vertical {vertical.name!r}")
        if applied:
            e.add_note(f"after overrides {applied}")
        raise
    cfg._applied_overrides = applied
    cfg._regime = regime
    return cfg


def config_hash(cfg: BaseScenarioConfig) -> str:
    """Stable content hash of a config (for the run manifest / reproducibility)."""
    payload = cfg.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
