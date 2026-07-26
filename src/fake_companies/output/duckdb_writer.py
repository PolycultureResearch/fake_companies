"""Write generated frames into a DuckDB database, schema-enforced.

The writer owns table creation from the :mod:`schemas` registry so column order
and types are canonical regardless of how a producer built its frame. Empty
frames still create the (empty) table, so a bare ``generate`` always yields a
complete, well-typed database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import duckdb
import pandas as pd

from .schemas import ALL_TABLES, BY_FQN, TableSpec


class DuckDBWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()  # deterministic: always a fresh database
        self.con = duckdb.connect(str(self.path))
        self._create_all()

    def _create_all(self) -> None:
        schemas = {t.schema for t in ALL_TABLES}
        for s in sorted(schemas):
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        for spec in ALL_TABLES:
            self.con.execute(spec.ddl())

    def write(self, fqn: str, df: pd.DataFrame) -> None:
        """Insert a frame into ``fqn``, coercing to the registry column order."""
        spec = BY_FQN[fqn]
        frame = _coerce(df, spec)
        self.con.register("_incoming", frame)
        cols = ", ".join(f'"{c}"' for c in spec.columns)
        self.con.execute(f"INSERT INTO {spec.fqn} SELECT {cols} FROM _incoming")
        self.con.unregister("_incoming")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _coerce(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    """Reindex to the registry columns, adding missing (all-null) columns."""
    frame = df.copy()
    for col in spec.columns:
        if col not in frame.columns:
            frame[col] = pd.NA
    return frame[list(spec.columns)]
