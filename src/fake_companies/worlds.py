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
    <regime>/s<seed>/null.series.parquet            (every world has one)
    <regime>/s<seed>/null.weekly_series.parquet     (when the vertical has distinct counts)
    <regime>/s<seed>/only.<anomaly>.duckdb          (+ the same two sidecars)
    <regime>/s<seed>/only.<anomaly>.effects.parquet
    <regime>/s<seed>/only.<anomaly>.effects_summary.parquet
    <regime>/s<seed>/story.duckdb                   (+ the same two sidecars)
    effects_summary.parquet

Each single world carries its **measured effects**: the day-by-day difference
between it and its null twin for every metric of the vertical's semantic
layer (``*.effects.parquet``), and a summary of that difference in its ground
truth record. ``effects_summary.parquet`` is the same summary across seeds.
See :mod:`fake_companies.effects` for what the numbers mean and how noisy
they are.

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
    # A .duckdb file, or a directory of <schema>.<table>.parquet; None for a
    # set built with --no-raw (series and effects only).
    path: str | None = None
    ground_truth: str
    run_manifest: str
    config_hash: str  # hash of the effective config (overrides + selection applied)
    # Single worlds: date, metric, dimension, value, null_value, world_value, diff.
    effects: str | None = None
    # Single worlds: one row per (metric, dimension, value) of the effects
    # table with the measured stats (rel_change, noise_floor, first_diff,
    # onset, z_window, moved, ...). The ground truth's ``measured_effects``
    # is this table's totals; a node bound to a slice looks itself up here.
    effects_summary: str | None = None
    # Every world: date, metric, dimension, value, metric_value, numerator,
    # denominator — the daily series of every metric, total and by each
    # single dimension, as MetricFlow would return them.
    series: str | None = None
    # Every world, when some metric has ``has_weekly_series``: date (the
    # Monday starting the week), metric, dimension, value, metric_value, days
    # — MetricFlow's metric_time__week value of each distinct-count metric,
    # total and by each single dimension. ``days`` is the number of timeline
    # days in the week (fewer than 7 only at the timeline's edges).
    weekly_series: str | None = None


class MetricInfo(BaseModel):
    """One metric of the effects tables, and how to ask MetricFlow for it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str  # simple | ratio | derived | cumulative
    fill_nulls_with: float | None = None  # value of a day with no rows (None: NULL)
    # How daily values combine into a week or month: sum | end_of_period |
    # ratio_of_sums | ratio_of_aggregates | average | count_distinct |
    # rolling_window | derived (see SemanticLayer.time_aggregation).
    time_aggregation: str = "sum"
    # The measure aggregation behind a simple or cumulative metric (sum,
    # count_distinct, average, ...); None for ratio and derived metrics. A
    # stock that is a count_distinct (active_subscriptions) is the distinct
    # count on each day: its end-of-period value is the count on the last
    # day, which is NOT MetricFlow's weekly number (distinct over the week);
    # that number is in the weekly series file (``has_weekly_series``).
    measure_agg: str | None = None
    # Whether each world's ``weekly_series`` file carries this metric: true
    # for every simple count_distinct metric, whose week value cannot be
    # derived from its days.
    has_weekly_series: bool = False
    # ratio: {"numerator": metric, "denominator": metric}; derived: the
    # alias -> metric inputs of ``expr``. Empty otherwise.
    inputs: dict[str, str] = Field(default_factory=dict)
    # For each key of ``inputs``, that input metric's own time_aggregation:
    # aggregate each side by it before dividing or evaluating the formula.
    input_time_aggregation: dict[str, str] = Field(default_factory=dict)
    expr: str | None = None  # derived: SQL over the input aliases
    # derived: ``expr`` as a tree of {"metric"} / {"const"} / {"op", "args"}
    # nodes (see SemanticLayer.formula); None if it could not be parsed.
    formula: dict | None = None
    # Whether the series carry numerator/denominator for this metric (ratio
    # metrics: the two inputs; averages: the sum and the row count).
    has_sides: bool = False
    # Dimension name as the effects tables spell it -> MetricFlow group-by id
    # (``channel`` -> ``session__channel``), or None when the metric spans
    # semantic models and no single MetricFlow group-by addresses it.
    dimensions: dict[str, str | None] = Field(default_factory=dict)


class NoiseDefinition(BaseModel):
    """The constants behind ``noise_floor`` / ``moved`` (see ``effects``)."""

    model_config = ConfigDict(extra="forbid")

    k: float  # NOISE_K: per-day floor and window z threshold, in noise sigmas
    integrated_ratio: float  # level noise / step noise above which a series is stock-like
    min_placebo_windows: int  # placebo windows needed to use their spread


class VerdictDefinition(BaseModel):
    """One verdict column of ``effects_summary.parquet`` and its test.

    Every verdict tests the cross-seed mean relative change against its
    standard error (``t_across_seeds``) with Student's t at ``n_seeds - 1``
    degrees of freedom, two-sided at ``level``. The column name carries the
    level too (``verdict`` is the strict 1% one, ``verdict_10pct`` the loose
    one), so a reader never has to guess which one it holds.
    """

    model_config = ConfigDict(extra="forbid")

    column: str  # the verdict column in effects_summary.parquet
    level: float  # two-sided significance level of the test (0.01, 0.10)
    # |t_across_seeds| needed to be established at this set's seed count;
    # None when the set has fewer than ``min_seeds`` seeds (never established).
    t_critical: float | None
    # The fewest seeds at which this verdict can be established at all (with
    # one seed there is no spread). Below it every effect is ``open``.
    min_seeds: int
    # Per-row column: the projected seed count at which that row's mean and
    # spread would be established at this level (NULL: nothing to establish).
    seeds_to_establish_column: str


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
    # Measured effects: absent (None / empty) for a set built without them.
    effects_summary: str | None = None
    noise: NoiseDefinition | None = None
    # The verdict columns of effects_summary.parquet, strict first. Empty for
    # a set built without measured effects.
    verdicts: list[VerdictDefinition] = Field(default_factory=list)
    metrics: list[MetricInfo] = Field(default_factory=list)
    unsupported_metrics: dict[str, str] = Field(default_factory=dict)  # name -> why left out
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
class RootNotConfirmedWarning(UserWarning):
    """A single world's declared root metric did not clear its noise floor."""


def _dimension_sets(root_slice: dict[str, str]) -> list[tuple[str, ...]]:
    """Slices an anomaly's effects are cut by: its whole root slice, then each
    single dimension of it."""
    dims = tuple(root_slice)
    sets = [dims] if dims else []
    sets += [(d,) for d in dims if (d,) not in sets]
    return sets


def _write_world(result, group_dir: Path, stem: str, fmt: str, con=None, raw=True) -> str | None:
    """Persist one world's tables; return its path relative to ``group_dir``
    (None with ``raw=False``: sidecars only)."""
    from .generate import write_generation
    from .output import write_json_sidecars
    from .output.export import write_raw_parquet

    if not raw:
        write_json_sidecars(group_dir, result.manifest, result.ground_truth, stem=stem)
        return None
    if fmt == "duckdb":
        rel = f"{stem}.duckdb"
        write_generation(result, group_dir / rel, sidecar_dir=group_dir, sidecar_stem=stem)
    else:
        rel = stem
        write_raw_parquet(result.frames, result.tables, group_dir / rel, con=con)
        write_json_sidecars(group_dir, result.manifest, result.ground_truth, stem=stem)
    return rel


def _write_effects(effects, path: Path) -> None:
    import duckdb

    con = duckdb.connect()
    try:
        con.register("_effects", effects)
        con.execute(
            "COPY (SELECT CAST(date AS DATE) AS date, metric, dimension, value, null_value, "
            "world_value, diff FROM _effects ORDER BY metric, dimension NULLS FIRST, "
            f"value NULLS FIRST, date) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def _write_series(series, path: Path) -> None:
    """One world's metric series: totals and single-dimension slices."""
    import duckdb

    con = duckdb.connect()
    try:
        con.register("_series", series)
        con.execute(
            "COPY (SELECT CAST(date AS DATE) AS date, metric, dimension, value, "
            "v AS metric_value, numerator, denominator FROM _series "
            "WHERE dimension IS NULL OR NOT contains(dimension, ',') "
            "ORDER BY metric, dimension NULLS FIRST, value NULLS FIRST, date) "
            f"TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def _write_weekly_series(weekly, path: Path) -> None:
    """One world's week-grain distinct counts: totals and single-dimension slices."""
    import duckdb

    con = duckdb.connect()
    try:
        con.register("_weekly", weekly)
        con.execute(
            "COPY (SELECT CAST(date AS DATE) AS date, metric, dimension, value, "
            "v AS metric_value, CAST(days AS INTEGER) AS days FROM _weekly "
            "ORDER BY metric, dimension NULLS FIRST, value NULLS FIRST, date) "
            f"TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def _write_twin_summary(summary, path: Path) -> None:
    import duckdb

    con = duckdb.connect()
    try:
        con.register("_summary", summary)
        con.execute(
            "COPY (SELECT * FROM _summary ORDER BY metric, dimension NULLS FIRST, "
            f"value NULLS FIRST) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def _build_group(job: dict[str, Any]) -> dict[str, Any]:
    """Build every world of one (regime, seed). Top-level so it pickles.

    Returns ``{"entries": [WorldEntry dicts], "summaries": [per-twin summary
    frames], "warnings": [str]}``.
    """
    from .effects import ground_truth_fields, summarize_twin, twin_effects
    from .generate import generate
    from .semantic import SemanticLayer
    from .verticals import get_vertical

    out_dir = Path(job["out_dir"])
    regime, seed, fmt = job["regime"], job["seed"], job["format"]
    cfg = load_config(job["config"], overrides=job["overrides"], regime=regime)
    rel_dir = Path(regime) / f"s{seed}"
    group_dir = out_dir / rel_dir
    group_dir.mkdir(parents=True, exist_ok=True)

    layer = fills = None
    singles_dims: list[tuple[str, ...]] = []
    if job["effects"] or job["series"]:
        layer = SemanticLayer.for_vertical(get_vertical(cfg.company.vertical), job["dbt_root"])
        fills = {name: layer.fill_value(name) for name in layer.metrics}
        if job["series"]:
            singles_dims = layer.single_dimension_sets()

    # Which slices each single is measured on follows from its anomaly's root
    # label, so the null world (built first) can carry every slice its twins
    # need without any of them having been generated yet. One world is in
    # memory at a time.
    vertical = get_vertical(cfg.company.vertical)
    scripted = {a.name: a for a in cfg.anomalies.scripted}
    sets_by_plan: dict[str, list[tuple[str, ...]]] = {}
    for p in job["plans"]:
        sets_by_plan[p.id] = []
        if p.kind == "single":
            anomaly = scripted[p.selection.removeprefix("only:")]
            if anomaly.kind == "rate":
                label = vertical.root_label(anomaly.target, anomaly.segment)
                sets_by_plan[p.id] = _dimension_sets(label.slice)
    null_sets = list(dict.fromkeys(s for sets in sets_by_plan.values() for s in sets))

    out: dict[str, Any] = {"entries": [], "summaries": [], "warnings": []}
    null_series = None
    for plan in job["plans"]:
        result = generate(cfg, seed=seed, anomalies=plan.selection)
        con = effects_rel = series_rel = summary_rel = weekly_rel = None
        try:
            twin = job["effects"] and plan.kind in ("null", "single")
            if twin or job["series"]:
                con = layer.build_marts(result.frames, result.tables)
            elif fmt == "parquet" and job["raw"]:
                con = _raw_connection(result)
            series = None
            if con is not None and layer is not None and (twin or job["series"]):
                own = null_sets if plan.kind == "null" else sets_by_plan[plan.id]
                wanted = list(dict.fromkeys([*singles_dims, *(own if twin else [])]))
                series = layer.series_from(con, result.calendar, wanted)
            if job["series"]:
                series_rel = (rel_dir / f"{plan.stem}.series.parquet").as_posix()
                _write_series(series, out_dir / series_rel)
                if layer.weekly_metrics():
                    weekly = layer.weekly_series_from(con, result.calendar, singles_dims)
                    weekly_rel = (rel_dir / f"{plan.stem}.weekly_series.parquet").as_posix()
                    _write_weekly_series(weekly, out_dir / weekly_rel)
            if twin and plan.kind == "null":
                null_series = series
            elif twin:
                record = result.ground_truth[0]
                # Measured on the total, on every single dimension the series
                # carry, and on the anomaly's own root slice.
                labels = {",".join(s) for s in [*singles_dims, *sets_by_plan[plan.id]]}

                def measured(frame, labels=labels):
                    return frame[frame["dimension"].isna() | frame["dimension"].isin(labels)]

                effects = twin_effects(measured(null_series), measured(series), fills)
                summary = summarize_twin(effects, record.start, record.end)
                summary_rel = (rel_dir / f"{plan.stem}.effects_summary.parquet").as_posix()
                _write_twin_summary(summary, out_dir / summary_rel)
                fields = ground_truth_fields(
                    summary, record.affected_metrics, record.root_metric, record.root_slice
                )
                result.ground_truth[0] = record.model_copy(update=fields)
                effects_rel = (rel_dir / f"{plan.stem}.effects.parquet").as_posix()
                _write_effects(effects, out_dir / effects_rel)
                summary.insert(0, "regime", regime)
                summary.insert(1, "seed", seed)
                summary.insert(2, "anomaly", record.id)
                summary.insert(3, "start", record.start)
                out["summaries"].append(summary)
                if fields.get("root_metric_confirmed") is False:
                    root = fields["root_effect"]
                    where = (
                        f" at {record.root_dimension}={record.root_value}"
                        if record.root_slice
                        else ""
                    )
                    out["warnings"].append(
                        f"{plan.id}: root metric {record.root_metric!r}{where} did not clear "
                        f"its noise floor (rel_change={root['rel_change']})"
                    )
            rel = _write_world(result, group_dir, plan.stem, fmt, con=con, raw=job["raw"])
        finally:
            if con is not None:
                con.close()
        out["entries"].append(
            WorldEntry(
                id=plan.id,
                seed=seed,
                regime=regime,
                overrides=cfg.applied_overrides,
                kind=plan.kind,
                anomalies=[r.id for r in result.ground_truth],
                twin_of=plan.twin_of,
                path=None if rel is None else (rel_dir / rel).as_posix(),
                ground_truth=(rel_dir / f"{plan.stem}.ground_truth.json").as_posix(),
                run_manifest=(rel_dir / f"{plan.stem}.run_manifest.json").as_posix(),
                config_hash=result.manifest["config_hash"],
                effects=effects_rel,
                effects_summary=summary_rel,
                series=series_rel,
                weekly_series=weekly_rel,
            ).model_dump(mode="json")
        )
    return out


def _raw_connection(result):
    """An in-memory DuckDB holding a result's raw tables (typed by their DDL)."""
    from .output.duckdb_writer import DuckDBWriter

    writer = DuckDBWriter(":memory:", list(result.tables))
    for spec in result.tables:
        writer.write(spec.fqn, result.frames[spec.fqn])
    writer.con.execute("SET enable_progress_bar = false")
    return writer.con


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
    effects: bool = True,
    series: bool = True,
    raw: bool = True,
    dbt_root: str | Path | None = None,
) -> WorldSetManifest:
    """Generate a world set into ``out_dir`` and write its ``worlds.json``.

    ``overrides`` (dotted path -> value) apply to every world, on top of each
    regime's own. ``jobs`` builds (regime, seed) groups in parallel processes;
    the output does not depend on it. ``effects=False`` skips the measured
    effects and ``series=False`` the per-world metric series (both need the
    repo's dbt projects, found under ``dbt_root``). ``raw=False`` skips the
    raw tables, for consumers that read only series and effects.
    A single world whose root metric does not clear its noise floor raises a
    :class:`RootNotConfirmedWarning`; the world is still written and its
    ground truth says ``root_metric_confirmed: false``.
    """
    import warnings

    from .effects import (
        INTEGRATED_RATIO,
        MIN_PLACEBO_WINDOWS,
        MIN_SEEDS_TO_ESTABLISH,
        NOISE_K,
        VERDICTS,
        summarize_across_seeds,
        t_critical,
    )
    from .verticals import get_vertical

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
    if not raw and not series and not effects:
        raise ValueError("raw=False with neither series nor effects would write no data")
    measured = effects and bool(single_names)
    layer = None
    if measured or series:
        from .semantic import SemanticLayer

        layer = SemanticLayer.for_vertical(get_vertical(vertical), dbt_root)
    stocks = get_vertical(vertical).stock_metrics()

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
            "effects": measured,
            "series": series,
            "raw": raw,
            "dbt_root": None if dbt_root is None else str(dbt_root),
        }
        for (regime, seed), group in groups.items()
    ]
    if jobs > 1 and len(job_list) > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, len(job_list))) as pool:
            results = list(pool.map(_build_group, job_list))  # map keeps plan order
    else:
        results = [_build_group(job) for job in job_list]

    summary_rel = None
    if measured:
        import pandas as pd

        frames = [s for group in results for s in group["summaries"]]
        summary = summarize_across_seeds(pd.concat(frames, ignore_index=True))
        summary_rel = "effects_summary.parquet"
        _write_summary(summary, out_dir / summary_rel)

    manifest = WorldSetManifest(
        generator_version=TOOL_VERSION,
        config=config.as_posix(),
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        vertical=vertical,
        company_slug=base.company.slug,
        format=fmt,  # type: ignore[arg-type]
        seeds=sorted(set(seeds)),
        regimes=regimes,
        effects_summary=summary_rel,
        noise=NoiseDefinition(
            k=NOISE_K,
            integrated_ratio=INTEGRATED_RATIO,
            min_placebo_windows=MIN_PLACEBO_WINDOWS,
        )
        if measured
        else None,
        verdicts=[
            VerdictDefinition(
                column=column,
                level=level,
                t_critical=(
                    t_critical(len(set(seeds)), level)
                    if len(set(seeds)) >= MIN_SEEDS_TO_ESTABLISH
                    else None
                ),
                min_seeds=MIN_SEEDS_TO_ESTABLISH,
                seeds_to_establish_column=seeds_column,
            )
            for column, level, seeds_column in VERDICTS
        ]
        if measured
        else [],
        metrics=[
            MetricInfo(
                name=name,
                type=layer.metrics[name].kind,
                fill_nulls_with=layer.fill_value(name),
                dimensions=layer.dimensions(name),
                has_weekly_series=series and name in layer.weekly_metrics(),
                **_metric_inputs(layer, name, stocks),
            )
            for name in sorted(layer.metrics)
        ]
        if layer is not None
        else [],
        unsupported_metrics=layer.unsupported() if layer is not None else {},
        worlds=[WorldEntry.model_validate(e) for group in results for e in group["entries"]],
    )
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
    )
    for group in results:
        for message in group["warnings"]:
            warnings.warn(message, RootNotConfirmedWarning, stacklevel=2)
    return manifest


def _metric_inputs(layer, name: str, stocks: frozenset[str]) -> dict[str, Any]:
    """The aggregation fields of one manifest ``MetricInfo``."""
    metric = layer.metrics[name]
    inputs = dict(metric.inputs)
    if metric.kind == "ratio":
        inputs = {"numerator": metric.inputs["num"], "denominator": metric.inputs["den"]}
    return {
        "time_aggregation": layer.time_aggregation(name, stocks),
        "measure_agg": layer.measure_agg(name),
        "inputs": inputs,
        "input_time_aggregation": {
            key: layer.time_aggregation(m, stocks) for key, m in inputs.items()
        },
        "expr": metric.expr,
        "formula": layer.formula(name),
        "has_sides": layer.has_sides(name),
    }


def _write_summary(summary, path: Path) -> None:
    import duckdb

    con = duckdb.connect()
    try:
        con.register("_summary", summary)
        con.execute(
            "COPY (SELECT * FROM _summary ORDER BY regime, anomaly, metric, "
            f"dimension NULLS FIRST, value NULLS FIRST) TO '{path}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()
