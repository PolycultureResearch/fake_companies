"""``fake-companies`` command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Generate realistic synthetic raw company data with ground-truth anomalies.",
    no_args_is_help=True,
)


@app.command()
def generate(
    config: Path = typer.Option(..., "--config", "-c", help="Scenario YAML path."),
    out: Path = typer.Option("out/company.duckdb", "--out", "-o", help="DuckDB output path."),
    seed: int | None = typer.Option(None, "--seed", help="Override the config seed."),
    dump_drivers: Path | None = typer.Option(
        None, "--dump-drivers", help="Write the latent driver panel to CSV for inspection."
    ),
    anomalies: str = typer.Option(
        "all",
        "--anomalies",
        help="all (default) | none | only:<name>[,<name>...] (named scripted anomalies, "
        "no surprise sampling).",
    ),
    set_: list[str] | None = typer.Option(
        None,
        "--set",
        help="Override a config value by dotted path before validation, e.g. "
        "--set timeline.days=1095 (repeatable; value is YAML; '*' matches every key).",
    ),
    regime: str | None = typer.Option(
        None,
        "--regime",
        help="Named baseline regime from configs/regimes/<vertical>.yaml "
        "(flat, declining, accelerating, long_history, no_seasonality).",
    ),
) -> None:
    """Generate a company's raw data into a DuckDB database."""
    from .anomalies import select_anomalies
    from .config import load_config
    from .config.overrides import parse_overrides
    from .generate import run_generation

    try:
        cfg = load_config(config, overrides=parse_overrides(set_), regime=regime)
        select_anomalies(cfg, anomalies)  # fail on a bad selection before generating
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    result = run_generation(cfg, out, seed=seed, anomalies=anomalies)
    cfg = result.cfg  # the effective config (anomaly selection applied)
    typer.echo(
        f"Generated {cfg.company.name}: {result.calendar.n_days} days, "
        f"{sum(len(f) for f in result.frames.values())} rows, "
        f"{len(result.ground_truth)} ground-truth events -> {out}"
    )
    if dump_drivers is not None:
        _dump_drivers(cfg, seed, dump_drivers)
        typer.echo(f"Wrote driver panel -> {dump_drivers}")


@app.command()
def worlds(
    config: Path = typer.Option(..., "--config", "-c", help="Scenario YAML path."),
    out: Path = typer.Option(..., "--out", "-o", help="World-set output directory."),
    seeds: str = typer.Option("0", "--seeds", help="Seeds: N, N-M or a comma list (0-9, 0-2,7)."),
    regime: list[str] | None = typer.Option(
        None, "--regime", help="Regime(s) to build (repeatable; default: baseline)."
    ),
    null: bool = typer.Option(False, "--null", help="Build the null world (no anomalies)."),
    singles: str | None = typer.Option(
        None,
        "--singles",
        help="One world per scripted anomaly: 'all' or a comma list of names. "
        "Always builds the null twin too.",
    ),
    story: bool = typer.Option(False, "--story", help="Build the config as written."),
    fmt: str = typer.Option("duckdb", "--format", help="duckdb | parquet (raw tables only)."),
    jobs: int = typer.Option(1, "--jobs", "-j", help="Parallel processes (output is identical)."),
    set_: list[str] | None = typer.Option(
        None, "--set", help="Override applied to every world (dotted.path=value, repeatable)."
    ),
    effects: bool = typer.Option(
        True,
        "--effects/--no-effects",
        help="Measure each single world against its null twin (needs the repo's dbt/ projects).",
    ),
    series: bool = typer.Option(
        True,
        "--series/--no-series",
        help="Write <world>.series.parquet: every metric's daily series, total and by "
        "each dimension (needs the repo's dbt/ projects).",
    ),
    no_raw: bool = typer.Option(
        False, "--no-raw", help="Skip the raw tables (series, effects and ground truth only)."
    ),
) -> None:
    """Build a world set: seeds x regimes x (null, singles, story) plus worlds.json."""
    import warnings

    from .config.overrides import parse_overrides
    from .worlds import MANIFEST_NAME, RootNotConfirmedWarning, build_world_set, parse_seeds

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RootNotConfirmedWarning)
            manifest = build_world_set(
                config,
                out,
                seeds=parse_seeds(seeds),
                regimes=regime,
                null=null,
                singles=singles,
                story=story,
                fmt=fmt,
                jobs=jobs,
                overrides=parse_overrides(set_),
                effects=effects,
                series=series,
                raw=not no_raw,
            )
    except (ValueError, FileNotFoundError) as e:
        raise typer.BadParameter(str(e)) from None
    for w in caught:
        typer.echo(f"warning: {w.message}", err=True)
    kinds = [w.kind for w in manifest.worlds]
    typer.echo(
        f"Built {len(manifest.worlds)} worlds ({kinds.count('null')} null, "
        f"{kinds.count('single')} single, {kinds.count('story')} story) for "
        f"{len(manifest.regimes)} regime(s) x {len(manifest.seeds)} seed(s) -> {out / MANIFEST_NAME}"
    )


@app.command()
def export(
    db: Path = typer.Option("out/company.duckdb", "--db", help="DuckDB database path."),
    out: Path = typer.Option("out/export", "--out", "-o", help="Output directory."),
    fmt: str = typer.Option("parquet", "--format", help="parquet | csv"),
) -> None:
    """Export raw tables to Parquet or CSV."""
    from .output import export_tables

    written = export_tables(db, out, fmt=fmt)
    typer.echo(f"Exported {len(written)} tables ({fmt}) -> {out}")


@app.command()
def truth(
    db: Path = typer.Option("out/company.duckdb", "--db", help="DuckDB database path."),
) -> None:
    """Print the ground-truth anomaly table."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "SELECT id, kind, type, target, start_date, end_date, magnitude "
        "FROM meta.ground_truth ORDER BY start_date, id"
    ).fetchall()
    con.close()
    if not rows:
        typer.echo("(no injected anomalies)")
        return
    for r in rows:
        typer.echo(
            json.dumps(
                {
                    k: str(v)
                    for k, v in zip(
                        ["id", "kind", "type", "target", "start", "end", "magnitude"], r
                    )
                }
            )
        )


@app.command()
def score(
    truth: Path = typer.Option(..., "--truth", help="ground_truth.json from a run."),
    events: Path = typer.Option(..., "--events", help="Detector events JSON to score."),
    tolerance: int = typer.Option(3, "--tolerance", help="+-k day bucket tolerance."),
) -> None:
    """Score detector events against ground truth (precision/recall)."""
    from .scoring import score_events

    report = score_events(truth, events, tolerance_days=tolerance)
    typer.echo(json.dumps(report, indent=2))


def _dump_drivers(cfg, seed, path: Path) -> None:
    import pandas as pd

    from .anomalies import resolve_anomalies
    from .core import RngHub, build_calendar
    from .latent import apply_rate_events
    from .verticals import get_vertical

    vertical = get_vertical(cfg.company.vertical)
    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed if seed is None else seed)
    resolved = resolve_anomalies(cfg, cal, rng, vertical)
    panel = vertical.build_drivers(cfg, cal, rng)
    panel, _ = apply_rate_events(
        panel, resolved, cal, known=vertical.known_drivers(cfg), affected=vertical.affected_metrics
    )
    df = pd.DataFrame({"date": cal.dates})
    for key, arr in sorted(panel.rates.items()):
        df[key] = arr
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


if __name__ == "__main__":
    app()
