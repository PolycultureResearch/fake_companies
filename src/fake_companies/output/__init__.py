"""Output layer: DuckDB writer, table-spec contract, exports, manifest."""

from .duckdb_writer import DuckDBWriter
from .export import export_hashes, export_tables
from .manifest import build_manifest, ground_truth_frame, manifest_frame, write_json_sidecars
from .schemas import META_TABLES, TableSpec

__all__ = [
    "META_TABLES",
    "DuckDBWriter",
    "TableSpec",
    "build_manifest",
    "export_hashes",
    "export_tables",
    "ground_truth_frame",
    "manifest_frame",
    "write_json_sidecars",
]
