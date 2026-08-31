"""Top-level generation orchestrator.

Resolves the vertical from ``company.vertical`` and wires the three layers
through its protocol: latent driver panel + rate anomalies → entity-level raw
frames → observation-layer corruption, then persists to DuckDB with the run
manifest and ground truth. Everything business-model-specific comes from the
vertical; this module stays vertical-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import BaseScenarioConfig
from .core import RngHub, build_calendar
from .core.calendar import Calendar
from .groundtruth import GroundTruthRecord
from .output import (
    DuckDBWriter,
    build_manifest,
    ground_truth_frame,
    manifest_frame,
    write_json_sidecars,
)
from .output.schemas import META_TABLES, TableSpec
from .verticals import get_vertical


@dataclass
class GenerationResult:
    cfg: BaseScenarioConfig
    seed: int
    calendar: Calendar
    tables: list[TableSpec] = field(default_factory=list)
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    ground_truth: list[GroundTruthRecord] = field(default_factory=list)
    manifest: dict[str, str] = field(default_factory=dict)


def generate(cfg: BaseScenarioConfig, seed: int | None = None) -> GenerationResult:
    """Run the full pipeline in memory and return frames + ground truth."""
    seed = cfg.seed if seed is None else seed
    vertical = get_vertical(cfg.company.vertical)
    cal = build_calendar(cfg)
    rng = RngHub(seed)

    frames: dict[str, pd.DataFrame] = {}
    ground_truth: list[GroundTruthRecord] = []

    # Resolve scripted + surprise anomalies once (stable rate/dq split).
    from .anomalies import resolve_anomalies

    resolved = resolve_anomalies(cfg, cal, rng, vertical)

    # --- Layer 1: latent driver panel + rate anomalies ---------------------- #
    from .latent import apply_rate_events

    panel = vertical.build_drivers(cfg, cal, rng)
    panel, rate_gt = apply_rate_events(
        panel, resolved, cal, known=vertical.known_drivers(cfg), affected=vertical.affected_metrics
    )
    ground_truth.extend(rate_gt)

    # --- Layer 2: entity-level simulation ----------------------------------- #
    vertical.build_entities(cfg, cal, rng, panel, frames)

    # --- Layer 3: observation-layer corruption + loading -------------------- #
    from .corruption import apply_loading_and_dq

    dq_gt = apply_loading_and_dq(cfg, cal, rng, frames, resolved, tables=vertical.tables())
    ground_truth.extend(dq_gt)

    manifest = build_manifest(cfg, seed, len(ground_truth))
    return GenerationResult(
        cfg=cfg,
        seed=seed,
        calendar=cal,
        tables=vertical.tables(),
        frames=frames,
        ground_truth=ground_truth,
        manifest=manifest,
    )


def write_generation(
    result: GenerationResult,
    out_path: str | Path,
    sidecar_dir: str | Path | None = None,
) -> Path:
    """Persist a :class:`GenerationResult` to a DuckDB database + JSON sidecars."""
    out_path = Path(out_path)
    with DuckDBWriter(out_path, result.tables + META_TABLES) as writer:
        for fqn, df in result.frames.items():
            writer.write(fqn, df)
        writer.write("meta.ground_truth", ground_truth_frame(result.ground_truth))
        writer.write("meta.run_manifest", manifest_frame(result.manifest))

    sidecar_dir = Path(sidecar_dir) if sidecar_dir else out_path.parent
    write_json_sidecars(sidecar_dir, result.manifest, result.ground_truth)
    return out_path


def run_generation(
    cfg: BaseScenarioConfig,
    out_path: str | Path,
    seed: int | None = None,
    sidecar_dir: str | Path | None = None,
) -> GenerationResult:
    result = generate(cfg, seed=seed)
    write_generation(result, out_path, sidecar_dir=sidecar_dir)
    return result
