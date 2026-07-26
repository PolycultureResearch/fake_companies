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
    out: Path = typer.Option("out/acme.duckdb", "--out", "-o", help="DuckDB output path."),
    seed: int | None = typer.Option(None, "--seed", help="Override the config seed."),
    dump_drivers: Path | None = typer.Option(
        None, "--dump-drivers", help="Write the latent driver panel to CSV for inspection."
    ),
) -> None:
    """Generate a company's raw data into a DuckDB database."""
    from .config import load_config
    from .generate import run_generation

    cfg = load_config(config)
    result = run_generation(cfg, out, seed=seed)
    typer.echo(
        f"Generated {cfg.company.name}: {result.calendar.n_days} days, "
        f"{sum(len(f) for f in result.frames.values())} rows, "
        f"{len(result.ground_truth)} ground-truth events -> {out}"
    )
    if dump_drivers is not None:
        _dump_drivers(cfg, seed, dump_drivers)
        typer.echo(f"Wrote driver panel -> {dump_drivers}")


@app.command()
def export(
    db: Path = typer.Option("out/acme.duckdb", "--db", help="DuckDB database path."),
    out: Path = typer.Option("out/export", "--out", "-o", help="Output directory."),
    fmt: str = typer.Option("parquet", "--format", help="parquet | csv"),
) -> None:
    """Export raw tables to Parquet or CSV."""
    from .output import export_tables

    written = export_tables(db, out, fmt=fmt)
    typer.echo(f"Exported {len(written)} tables ({fmt}) -> {out}")


@app.command()
def truth(
    db: Path = typer.Option("out/acme.duckdb", "--db", help="DuckDB database path."),
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

    from .core import RngHub, build_calendar
    from .latent import build_drivers

    cal = build_calendar(cfg)
    rng = RngHub(cfg.seed if seed is None else seed)
    panel = build_drivers(cfg, cal, rng)
    df = pd.DataFrame({"date": cal.dates})
    for key, arr in sorted(panel.rates.items()):
        df[key] = arr
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


if __name__ == "__main__":
    app()
