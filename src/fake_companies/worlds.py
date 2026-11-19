"""World sets: related generated companies plus one manifest (eval-worlds spec 4).

A *world* is one generated company: a config, a seed, a regime and a set of
anomalies. A *world set* is a directory of worlds and a ``worlds.json`` that
says how they relate:

- the **null** world of a (regime, seed) has no anomalies of any kind;
- a **single** world has exactly one scripted anomaly, and is the *twin* of
  the null world of the same regime and seed;
- the **story** world is the config as written (every scripted anomaly plus
  surprise sampling).

Layout under the output directory::

    worlds.json
    <regime>/s<seed>/null.duckdb                    (or null/ with --format parquet)
    <regime>/s<seed>/null.ground_truth.json
    <regime>/s<seed>/null.run_manifest.json
    <regime>/s<seed>/only.<anomaly>.duckdb          (+ the same two sidecars)
    <regime>/s<seed>/story.duckdb                   (+ the same two sidecars)

The manifest is the contract: a consumer finds worlds through
:class:`WorldSetManifest` (``load_world_set``) and nothing else. World ids and
bytes depend only on the config, seed, regime and anomaly — never on which
other worlds were requested, the order they were built in, or ``jobs``.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .config.overrides import BASELINE_REGIME, regime_overrides
from .output.manifest import TOOL_VERSION

SCHEMA_VERSION = 1
MANIFEST_NAME = "worlds.json"
WorldKind = Literal["null", "single", "story"]
WorldFormat = Literal["duckdb", "parquet"]


# --------------------------------------------------------------------------- #
# The manifest (exported for consumers)
# --------------------------------------------------------------------------- #
class WorldEntry(BaseModel):
    """One world of a set. Paths are relative to the manifest's directory."""

    model_config = ConfigDict(extra="forbid")

    id: str  # <company slug>.<regime>.s<seed>.<null | only.<anomaly> | story>
    seed: int
    regime: str
    overrides: dict[str, Any] = Field(default_factory=dict)  # dotted path -> value
    kind: WorldKind
    anomalies: list[str] = Field(default_factory=list)  # ground-truth ids in this world
    twin_of: str | None = None  # a single world's null twin
    path: str  # a .duckdb file, or a directory of <schema>.<table>.parquet
    ground_truth: str
    run_manifest: str
    config_hash: str  # hash of the effective config (overrides + selection applied)


class WorldSetManifest(BaseModel):
    """``worlds.json``: every world of a set and how they relate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    generator_version: str
    config: str  # the scenario YAML, as given
    config_sha256: str  # of that file's bytes
    vertical: str
    company_slug: str
    format: WorldFormat
    seeds: list[int]
    regimes: list[str]
    worlds: list[WorldEntry]

    def world(self, world_id: str) -> WorldEntry:
        for w in self.worlds:
            if w.id == world_id:
                return w
        raise KeyError(world_id)

    def twins(self) -> list[tuple[WorldEntry, WorldEntry]]:
        """Every ``(single world, its null twin)`` pair in the set."""
        return [(w, self.world(w.twin_of)) for w in self.worlds if w.twin_of is not None]


def load_world_set(path: str | Path) -> WorldSetManifest:
    """Read and validate a world set's manifest (a directory or the file)."""
    path = Path(path)
    if path.is_dir():
        path = path / MANIFEST_NAME
    return WorldSetManifest.model_validate_json(path.read_text())


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def parse_seeds(text: str) -> list[int]:
    """``"0-9"``, ``"3"``, ``"0-2,7"`` -> a sorted list of distinct seeds."""
    seeds: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        lo, sep, hi = part.partition("-")
        try:
            first, last = int(lo), int(hi) if sep else int(lo)
        except ValueError:
            raise ValueError(f"seeds {text!r}: {part!r} is not N or N-M") from None
        if last < first:
            raise ValueError(f"seeds {text!r}: {part!r} runs backwards")
        seeds.update(range(first, last + 1))
    return sorted(seeds)


@dataclass(frozen=True)
class WorldPlan:
    id: str
    seed: int
    regime: str
    kind: WorldKind
    selection: str  # the anomaly selection handed to generate()
    stem: str  # file stem inside <regime>/s<seed>/
    twin_of: str | None = None


def world_id(slug: str, regime: str, seed: int, stem: str) -> str:
    return f"{slug}.{regime}.s{seed}.{stem}"


def resolve_singles(cfg, singles: str | list[str] | None) -> list[str]:
    """The scripted anomalies to build single worlds for, in config order."""
    if not singles:
        return []
    known = [a.name for a in cfg.anomalies.scripted]
    if singles == "all" or singles == ["all"]:
        return known
    names = singles.split(",") if isinstance(singles, str) else list(singles)
    names = [n.strip() for n in names if n.strip()]
    unknown = sorted(set(names) - set(known))
    if unknown:
        raise ValueError(f"--singles names unknown scripted anomalies {unknown}; known: {known}")
    return [n for n in known if n in names]


def plan_worlds(
    slug: str,
    regimes: list[str],
    seeds: list[int],
    *,
    null: bool,
    singles: list[str],
    story: bool,
) -> list[WorldPlan]:
    """Every world of the set, in manifest order (regime, seed, null/singles/story).

    Single worlds need their null twin, so asking for singles always builds
    the null world too, whether or not ``null`` was asked for.
    """
    plans: list[WorldPlan] = []
    for regime in regimes:
        for seed in seeds:
            null_id = world_id(slug, regime, seed, "null")
            if null or singles:
                plans.append(WorldPlan(null_id, seed, regime, "null", "none", "null"))
            for name in singles:
                stem = f"only.{name}"
                plans.append(
                    WorldPlan(
                        world_id(slug, regime, seed, stem),
                        seed,
                        regime,
                        "single",
                        f"only:{name}",
                        stem,
                        twin_of=null_id,
                    )
                )
            if story:
                plans.append(
                    WorldPlan(
                        world_id(slug, regime, seed, "story"), seed, regime, "story", "all", "story"
                    )
                )
    return plans


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def _write_world(result, group_dir: Path, stem: str, fmt: str) -> str:
    """Persist one world's tables; return its path relative to ``group_dir``."""
    from .generate import write_generation
    from .output import write_json_sidecars
    from .output.export import write_raw_parquet

    if fmt == "duckdb":
        rel = f"{stem}.duckdb"
        write_generation(result, group_dir / rel, sidecar_dir=group_dir, sidecar_stem=stem)
    else:
        rel = stem
        write_raw_parquet(result.frames, result.tables, group_dir / rel)
        write_json_sidecars(group_dir, result.manifest, result.ground_truth, stem=stem)
    return rel


def _build_group(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Build every world of one (regime, seed). Top-level so it pickles."""
    from .generate import generate

    out_dir = Path(job["out_dir"])
    regime, seed = job["regime"], job["seed"]
    cfg = load_config(job["config"], overrides=job["overrides"], regime=regime)
    rel_dir = Path(regime) / f"s{seed}"
    group_dir = out_dir / rel_dir
    group_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for plan in job["plans"]:
        result = generate(cfg, seed=seed, anomalies=plan.selection)
        rel = _write_world(result, group_dir, plan.stem, job["format"])
        entries.append(
            WorldEntry(
                id=plan.id,
                seed=seed,
                regime=regime,
                overrides=cfg.applied_overrides,
                kind=plan.kind,
                anomalies=[r.id for r in result.ground_truth],
                twin_of=plan.twin_of,
                path=(rel_dir / rel).as_posix(),
                ground_truth=(rel_dir / f"{plan.stem}.ground_truth.json").as_posix(),
                run_manifest=(rel_dir / f"{plan.stem}.run_manifest.json").as_posix(),
                config_hash=result.manifest["config_hash"],
            ).model_dump(mode="json")
        )
    return entries


def build_world_set(
    config: str | Path,
    out_dir: str | Path,
    *,
    seeds: list[int],
    regimes: list[str] | None = None,
    null: bool = False,
    singles: str | list[str] | None = None,
    story: bool = False,
    fmt: str = "duckdb",
    jobs: int = 1,
    overrides: dict[str, Any] | None = None,
) -> WorldSetManifest:
    """Generate a world set into ``out_dir`` and write its ``worlds.json``.

    ``overrides`` (dotted path -> value) apply to every world, on top of each
    regime's own. ``jobs`` builds (regime, seed) groups in parallel processes;
    the output does not depend on it.
    """
    if fmt not in ("duckdb", "parquet"):
        raise ValueError(f"unsupported world format {fmt!r} (duckdb | parquet)")
    if not seeds:
        raise ValueError("a world set needs at least one seed")
    regimes = list(dict.fromkeys(regimes or [BASELINE_REGIME]))
    config = Path(config)
    base = load_config(config, overrides=overrides)
    vertical = base.company.vertical
    for regime in regimes:
        if regime != BASELINE_REGIME:
            regime_overrides(vertical, regime)  # fail on an unknown regime before building
    single_names = resolve_singles(base, singles)
    plans = plan_worlds(
        base.company.slug, regimes, sorted(set(seeds)), null=null, singles=single_names, story=story
    )
    if not plans:
        raise ValueError("nothing to build: ask for --null, --singles and/or --story")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, int], list[WorldPlan]] = {}
    for plan in plans:
        groups.setdefault((plan.regime, plan.seed), []).append(plan)
    job_list = [
        {
            "config": str(config),
            "out_dir": str(out_dir),
            "regime": regime,
            "seed": seed,
            "plans": group,
            "format": fmt,
            "overrides": dict(overrides or {}),
        }
        for (regime, seed), group in groups.items()
    ]
    if jobs > 1 and len(job_list) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(job_list))) as pool:
            results = list(pool.map(_build_group, job_list))  # map keeps plan order
    else:
        results = [_build_group(job) for job in job_list]

    manifest = WorldSetManifest(
        generator_version=TOOL_VERSION,
        config=config.as_posix(),
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        vertical=vertical,
        company_slug=base.company.slug,
        format=fmt,  # type: ignore[arg-type]
        seeds=sorted(set(seeds)),
        regimes=regimes,
        worlds=[WorldEntry.model_validate(e) for group in results for e in group],
    )
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
    )
    return manifest
