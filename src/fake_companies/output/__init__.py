"""Output layer: DuckDB writer, schema registry, exports, manifest."""

from .duckdb_writer import DuckDBWriter
from .export import export_tables
from .manifest import build_manifest, ground_truth_frame, manifest_frame, write_json_sidecars
from .schemas import ALL_TABLES, RAW_TABLES, TableSpec

__all__ = [
    "ALL_TABLES",
    "RAW_TABLES",
    "DuckDBWriter",
    "TableSpec",
    "build_manifest",
    "export_tables",
    "ground_truth_frame",
    "manifest_frame",
    "write_json_sidecars",
]
