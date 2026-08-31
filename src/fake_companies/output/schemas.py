"""Table-spec contract + the vertical-independent meta tables.

A :class:`TableSpec` is the contract shared by the entity generators (which
produce the frames), the DuckDB writer (which enforces column order/types), the
loading model (which anchors ``_loaded_at`` on ``loading_ref``), and the dbt
staging models (which read them). Every raw table carries a ``_loaded_at``
column; tables Tremor profiles in dataflow mode additionally declare an
``event_time`` column.

Raw-table specs are vertical-owned — see ``fake_companies.verticals.<name>.tables``.
Only the meta tables (ground truth, run manifest) live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableSpec:
    schema: str
    name: str
    columns: dict[str, str]  # column name -> DuckDB type, in canonical order
    pk: str | None = None
    event_time: str | None = None  # business event time (Tremor dataflow bucketing)
    loaded_at: str = "_loaded_at"
    # Loading anchor: the column (or coalesce chain of columns) whose timestamp
    # the connector model treats as the row's business event. Falls back to
    # ``event_time``. ``reference_data=True`` means the table is slowly-changing
    # reference data anchored at a single nominal timestamp instead.
    loading_ref: str | tuple[str, ...] | None = None
    reference_data: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    def ddl(self) -> str:
        cols = ",\n  ".join(f'"{c}" {t}' for c, t in self.columns.items())
        return f"CREATE OR REPLACE TABLE {self.fqn} (\n  {cols}\n)"


# --------------------------------------------------------------------------- #
# Meta tables (identical for every vertical)
# --------------------------------------------------------------------------- #
GROUND_TRUTH = TableSpec(
    schema="meta",
    name="ground_truth",
    pk="id",
    loaded_at="",
    columns={
        "id": "VARCHAR",
        "kind": "VARCHAR",
        "type": "VARCHAR",
        "origin": "VARCHAR",
        "target": "VARCHAR",
        "segment": "VARCHAR",
        "start_date": "DATE",
        "end_date": "DATE",
        "magnitude": "DOUBLE",
        "affected_metrics": "VARCHAR",
        "affected_signals": "VARCHAR",
        "params": "VARCHAR",
    },
)

RUN_MANIFEST = TableSpec(
    schema="meta",
    name="run_manifest",
    loaded_at="",
    columns={
        "key": "VARCHAR",
        "value": "VARCHAR",
    },
)

META_TABLES: list[TableSpec] = [GROUND_TRUTH, RUN_MANIFEST]
