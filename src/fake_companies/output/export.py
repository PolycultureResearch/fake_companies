"""Export raw tables from a DuckDB database to Parquet or CSV.

Parquet exports back the determinism tests (byte-identical files under a fixed
seed). Files are written one per table as ``<schema>.<name>.<ext>``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb


def export_tables(db_path: str | Path, out_dir: str | Path, fmt: str = "parquet") -> list[Path]:
    """Export every base table found in the database (vertical-agnostic)."""
    if fmt not in ("parquet", "csv"):
        raise ValueError(f"unsupported export format {fmt!r}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True)
    written: list[Path] = []
    try:
        tables = con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' ORDER BY table_schema, table_name"
        ).fetchall()
        for schema, name in tables:
            fqn = f"{schema}.{name}"
            dest = out / f"{fqn}.{fmt}"
            if fmt == "parquet":
                con.execute(
                    f"COPY (SELECT * FROM {fqn} ORDER BY 1) "
                    f"TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            else:
                con.execute(
                    f"COPY (SELECT * FROM {fqn} ORDER BY 1) TO '{dest}' (FORMAT CSV, HEADER)"
                )
            written.append(dest)
    finally:
        con.close()
    return written


def export_hashes(db_path: str | Path, out_dir: str | Path) -> dict[str, str]:
    """Export all tables to Parquet and return ``{file name: sha256}``.

    Backs the determinism tests and the golden migration pin.
    """
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in export_tables(db_path, out_dir, fmt="parquet")
    }
