"""Export raw tables from a DuckDB database to Parquet or CSV.

Parquet exports back the determinism tests (byte-identical files under a fixed
seed). Files are written one per table as ``<schema>.<name>.<ext>``.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .schemas import ALL_TABLES


def export_tables(db_path: str | Path, out_dir: str | Path, fmt: str = "parquet") -> list[Path]:
    if fmt not in ("parquet", "csv"):
        raise ValueError(f"unsupported export format {fmt!r}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True)
    written: list[Path] = []
    try:
        for spec in ALL_TABLES:
            dest = out / f"{spec.schema}.{spec.name}.{fmt}"
            if fmt == "parquet":
                con.execute(
                    f"COPY (SELECT * FROM {spec.fqn} ORDER BY 1) "
                    f"TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            else:
                con.execute(
                    f"COPY (SELECT * FROM {spec.fqn} ORDER BY 1) TO '{dest}' (FORMAT CSV, HEADER)"
                )
            written.append(dest)
    finally:
        con.close()
    return written
