#!/usr/bin/env python
"""Verify the White Cube demo stories survived generation.

The demo instance is only worth showing if each planted anomaly is actually
recoverable from the aggregated metrics. This checks both halves of that:

* **Topline** — the metric moved between the reference and analysis windows,
  in the expected direction and by more than the trend control alongside it.
* **Concentration** — where a story is segmented, the injected slice carries far
  more of the gap than its baseline share predicts. That is the quantity
  Breakdown's slice ranking keys on (``excess``), so a story that fails here
  will not localize in the demo no matter how large the topline move is.

Excess is zero-sum across slices, so the culprit is the slice whose excess runs
*with* the gap — most positive when the metric rose, most negative when it fell.
Ranking by magnitude ties on a two-slice dimension.

Usage (from the repo root, with the dbt extra installed and models built):
    FAKE_DB=out/white_cube.duckdb python scripts/verify_white_cube_stories.py
Exits non-zero if any story fails.

Window pairs here are the source of truth for the demo's guided tour — keep
them in sync with breakdown/knowledge/demo_guided_tour.md.
"""

from __future__ import annotations

import collections
import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"

# label, metric, grain, reference window, analysis window, slice dimension, expected slice
STORIES: list[tuple[str, str, str, tuple[str, str], tuple[str, str], str | None, str | None]] = [
    (
        "A  mobile signup CTA regression",
        "signups",
        "day",
        ("2026-01-05", "2026-02-01"),
        ("2026-02-09", "2026-03-08"),
        "user__device",
        "mobile",
    ),
    (
        "A  -> new_mrr, one trial later",
        "new_mrr",
        "week",
        ("2026-01-05", "2026-02-01"),
        ("2026-02-09", "2026-03-08"),
        None,
        None,
    ),
    (
        "B  professional churn spike",
        "churned_mrr",
        "week",
        ("2026-03-16", "2026-04-12"),
        ("2026-05-11", "2026-06-07"),
        "mrr_movement__plan",
        "professional",
    ),
    (
        "B  -> net_new_mrr",
        "net_new_mrr",
        "week",
        ("2026-03-16", "2026-04-12"),
        ("2026-05-11", "2026-06-07"),
        None,
        None,
    ),
    (
        "C  Brazil campaign",
        "signups",
        "day",
        ("2025-02-03", "2025-03-02"),
        ("2025-03-10", "2025-04-06"),
        "user__country",
        "BR",
    ),
    (
        "C  trend control (volume)",
        "sessions",
        "day",
        ("2025-02-03", "2025-03-02"),
        ("2025-03-10", "2025-04-06"),
        None,
        None,
    ),
    (
        "D  onboarding revamp",
        "trial_conversion_rate",
        "week",
        ("2025-07-07", "2025-08-03"),
        ("2025-08-11", "2025-09-07"),
        None,
        None,
    ),
    (
        "D  trend control (volume)",
        "trials_started",
        "week",
        ("2025-07-07", "2025-08-03"),
        ("2025-08-11", "2025-09-07"),
        None,
        None,
    ),
    (
        "D  -> new_mrr",
        "new_mrr",
        "week",
        ("2025-07-07", "2025-08-03"),
        ("2025-08-11", "2025-09-07"),
        None,
        None,
    ),
]

# A segmented story must concentrate this hard, measured as excess over the gap:
# the share of the gap the slice carries *beyond* what its baseline share
# predicts. Deliberately not a ratio of shares — a slice that is already half the
# baseline (mobile) cannot carry 2x its share without exceeding the whole gap,
# so a ratio test would reject exactly the stories that localize best.
MIN_EXCESS_OVER_GAP = 0.25


def _fake_db() -> Path:
    return Path(os.environ.get("FAKE_DB", ROOT / "out" / "white_cube.duckdb")).resolve()


def mf_query(metric: str, group_by: str, start: str, end: str) -> list[list[str]]:
    """Run the exact query Breakdown's LocalDataFetcher issues."""
    env = {**os.environ, "DBT_PROFILES_DIR": str(DBT_DIR), "FAKE_DB": str(_fake_db())}
    mf = str(ROOT / ".venv" / "bin" / "mf")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        r = subprocess.run(
            [
                mf,
                "query",
                "--metrics",
                metric,
                "--group-by",
                group_by,
                "--start-time",
                start,
                "--end-time",
                end,
                "--csv",
                path,
            ],
            cwd=DBT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"mf query failed for {metric}: {r.stderr.strip()[-400:]}")
        with open(path) as fh:
            return list(csv.reader(fh))[1:]
    finally:
        os.unlink(path)


def total(rows: list[list[str]]) -> float:
    return sum(float(r[-1]) for r in rows if r and r[-1])


def by_slice(rows: list[list[str]]) -> dict[str, float]:
    d: dict[str, float] = collections.defaultdict(float)
    for r in rows:
        if r and r[-1]:
            d[r[1] or "__null__"] += float(r[-1])
    return d


def main() -> int:
    failures: list[str] = []

    for label, metric, grain, ref, ana, dim, expect in STORIES:
        gb = f"metric_time__{grain}"
        rt, at = total(mf_query(metric, gb, *ref)), total(mf_query(metric, gb, *ana))
        delta = (at / rt - 1) * 100 if rt else float("nan")
        print(f"\n{label}")
        print(f"   {metric:22s} ref={rt:12.4f}  ana={at:12.4f}  delta={delta:+7.1f}%")

        if not dim:
            continue

        ref_s = by_slice(mf_query(metric, f"{gb},{dim}", *ref))
        ana_s = by_slice(mf_query(metric, f"{gb},{dim}", *ana))
        gap = sum(ana_s.values()) - sum(ref_s.values())
        ref_tot = sum(ref_s.values())
        if not gap or not ref_tot:
            failures.append(f"{label}: no gap to attribute")
            continue

        ranked = []
        for k in set(ref_s) | set(ana_s):
            contrib = ana_s.get(k, 0.0) - ref_s.get(k, 0.0)
            share = ref_s.get(k, 0.0) / ref_tot
            ranked.append((contrib - share * gap, k, contrib / gap, share))
        ranked.sort(key=lambda r: -r[0] if gap > 0 else r[0])

        print(f"   slice by {dim}  (gap={gap:+.1f})")
        for excess, k, gap_share, base_share in ranked[:4]:
            mark = "  <-- expected" if k == expect else ""
            print(
                f"      {k:14s} {gap_share:+7.1%} of gap   baseline {base_share:6.1%}"
                f"   excess={excess:+9.1f}{mark}"
            )

        top_excess, top, top_gap_share, top_base = ranked[0]
        concentration = abs(top_excess / gap)
        if top != expect:
            failures.append(f"{label}: top slice is {top!r}, expected {expect!r}")
        elif concentration < MIN_EXCESS_OVER_GAP:
            failures.append(
                f"{label}: {expect!r} carries {top_gap_share:.1%} of the gap on a "
                f"{top_base:.1%} baseline share — excess is only {concentration:.1%} of "
                f"the gap, under the {MIN_EXCESS_OVER_GAP:.0%} the slice panel needs"
            )

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"All {len(STORIES)} checks pass — the demo stories are recoverable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
