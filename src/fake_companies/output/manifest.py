"""Run manifest + ground-truth serialization.

``run_manifest.json`` and ``meta.run_manifest`` capture the seed, config hash,
timeline, and tool version so a database is fully reproducible. ``ground_truth``
is written both as JSON (for blind-test scoring) and to ``meta.ground_truth``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..config import BaseScenarioConfig, config_hash
from ..groundtruth import GroundTruthRecord

TOOL_VERSION = "0.1.0"


def build_manifest(
    cfg: BaseScenarioConfig, seed: int, n_records: int, anomaly_selection: str = "all"
) -> dict[str, str]:
    """The run manifest for ``cfg`` (the *effective* config: after any
    overrides and anomaly selection, so ``config_hash`` identifies the bytes).

    ``anomaly_selection``, ``regime`` and ``overrides`` (JSON, dotted path ->
    value) are recorded only when they are not the default, so a default run's
    manifest is exactly what it was before any of them existed.
    """
    manifest = {
        "tool_version": TOOL_VERSION,
        "company": cfg.company.name,
        "company_slug": cfg.company.slug,
        "vertical": cfg.company.vertical,
        "seed": str(seed),
        "config_hash": config_hash(cfg),
        "timeline_start": cfg.timeline.start.isoformat(),
        "timeline_end": cfg.timeline.end_date.isoformat(),
        "n_days": str(cfg.timeline.n_days),
        "n_ground_truth": str(n_records),
    }
    if anomaly_selection != "all":
        manifest["anomaly_selection"] = anomaly_selection
    if cfg.regime is not None:
        manifest["regime"] = cfg.regime
    if cfg.applied_overrides:
        manifest["overrides"] = json.dumps(cfg.applied_overrides, sort_keys=True, default=str)
    return manifest


def manifest_frame(manifest: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"key": list(manifest.keys()), "value": [str(v) for v in manifest.values()]}
    )


def ground_truth_frame(records: list[GroundTruthRecord]) -> pd.DataFrame:
    rows = [r.as_row() for r in records]
    if not rows:
        return pd.DataFrame(
            columns=[
                "id",
                "kind",
                "type",
                "origin",
                "target",
                "segment",
                "start_date",
                "end_date",
                "magnitude",
                "affected_metrics",
                "affected_signals",
                "params",
            ]
        )
    return pd.DataFrame(rows)


def write_json_sidecars(
    out_dir: Path,
    manifest: dict[str, str],
    records: list[GroundTruthRecord],
    stem: str | None = None,
) -> tuple[Path, Path]:
    """Write ``run_manifest.json`` and ``ground_truth.json`` into ``out_dir``.

    ``stem`` prefixes both names (``<stem>.ground_truth.json``) so several
    worlds can share one directory.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{stem}." if stem else ""
    manifest_path = out_dir / f"{prefix}run_manifest.json"
    truth_path = out_dir / f"{prefix}ground_truth.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    truth_path.write_text(json.dumps([r.as_json() for r in records], indent=2, default=str))
    return manifest_path, truth_path
