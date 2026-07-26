#!/usr/bin/env python
"""Verify the consumer contracts against a built database + dbt project.

Breakdown: for every metric in ``examples/breakdown_acme_tree.yml`` run the exact
``mf query --metrics M --group-by metric_time__day --csv`` LocalDataFetcher uses,
and confirm a CSV with a ``metric_time__day`` column and a ``M`` column.

Tremor: for every dataflow monitor in ``examples/tremor_acme.yaml`` confirm the
raw relation exists in the DuckDB and exposes the event-time, ``_loaded_at``, PK,
and signal columns the monitor references.

Usage (from repo root, with the dbt extra installed):
    FAKE_DB=out/acme.duckdb python scripts/verify_consumers.py
Exits non-zero if any check fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"


def _fake_db() -> Path:
    return Path(os.environ.get("FAKE_DB", ROOT / "out" / "acme.duckdb")).resolve()


def verify_breakdown(start: str, end: str) -> list[tuple[str, bool, str]]:
    tree = yaml.safe_load((ROOT / "examples" / "breakdown_acme_tree.yml").read_text())
    metrics = [m["name"] for m in tree["metrics"]]
    env = {**os.environ, "DBT_PROFILES_DIR": str(DBT_DIR), "FAKE_DB": str(_fake_db())}
    mf = str(ROOT / ".venv" / "bin" / "mf")
    results = []
    for m in metrics:
        with tempfile.NamedTemporaryFile(suffix=".csv") as tmp:
            proc = subprocess.run(
                [mf, "query", "--metrics", m, "--group-by", "metric_time__day",
                 "--start-time", start, "--end-time", end, "--csv", tmp.name],
                cwd=DBT_DIR, env=env, capture_output=True, text=True, timeout=120,
                check=False,
            )
            header = Path(tmp.name).read_text().splitlines()[:1]
            hdr = header[0] if header else ""
            rows = max(0, len(Path(tmp.name).read_text().splitlines()) - 1)
            ok = (
                proc.returncode == 0
                and "metric_time__day" in hdr.lower()
                and m in hdr
                and rows > 0
            )
            results.append((f"breakdown:{m}", ok, f"{rows} rows"))
    return results


def verify_tremor() -> list[tuple[str, bool, str]]:
    cfg = yaml.safe_load((ROOT / "examples" / "tremor_acme.yaml").read_text())
    con = duckdb.connect(str(_fake_db()), read_only=True)
    results = []
    try:
        for mon in cfg["monitors"]:
            if mon.get("mode") != "dataflow":
                continue
            rel = mon["relation"]
            try:
                cols = {c[0] for c in con.execute(f"DESCRIBE {rel}").fetchall()}
            except duckdb.Error as e:
                results.append((f"tremor:{rel}", False, str(e)[:60]))
                continue
            required = {mon["event_time_column"], mon["loaded_at_column"]}
            required |= set(mon.get("guards", {}).get("pk_unique", []))
            for sig in mon.get("signals", []):
                required |= set(sig.get("columns", []))
                if sig.get("column"):
                    required.add(sig["column"])
            missing = required - cols
            results.append((f"tremor:{rel}", not missing,
                            "ok" if not missing else f"missing {sorted(missing)}"))
    finally:
        con.close()
    return results


def main() -> int:
    db = _fake_db()
    if not db.exists():
        print(f"FAKE_DB not found: {db}\nGenerate + `dbt build` first.", file=sys.stderr)
        return 2
    # Infer a query range from the run manifest.
    con = duckdb.connect(str(db), read_only=True)
    man = dict(con.execute("SELECT key, value FROM meta.run_manifest").fetchall())
    con.close()
    start, end = man.get("timeline_start", "2024-01-01"), man.get("timeline_end", "2024-03-30")

    results = verify_breakdown(start, end) + verify_tremor()
    ok = True
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'} {name} ({detail})")
        ok = ok and passed
    print("ALL CONSUMER CONTRACTS OK" if ok else "SOME CONSUMER CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
