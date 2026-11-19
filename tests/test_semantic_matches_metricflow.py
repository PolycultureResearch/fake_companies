"""The in-process metric series agree with MetricFlow (eval-worlds spec 4.5).

Measured effects are computed by ``fake_companies.semantic`` instead of
``dbt build`` + ``mf query`` per world. That is only honest if the two agree,
so for each vertical's smoke config this builds the dbt project, asks
MetricFlow for **every metric the semantic module computes** at day grain,
and requires the same numbers. It also checks one sliced query per vertical:
the root metric of the smoke anomaly, grouped by its root dimensions.

Requires the dbt extra (skipped otherwise).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.generate import generate, write_generation
from fake_companies.semantic import SemanticLayer
from fake_companies.verticals import REGISTRY, get_vertical

ROOT = Path(__file__).resolve().parents[1]
MF = ROOT / ".venv" / "bin" / "mf"
DBT = ROOT / ".venv" / "bin" / "dbt"
_available = importlib.util.find_spec("dbt") is not None and MF.exists() and DBT.exists()

SMOKES = ["smoke_90d", "smoke_retail_90d", "smoke_b2b_90d", "smoke_cpg_90d"]


def _mf(
    dbt_dir: Path, env: dict, metrics: list[str], group_by: list[str], out: Path
) -> pd.DataFrame:
    proc = subprocess.run(
        [
            str(MF),
            "query",
            "--metrics",
            ",".join(metrics),
            "--group-by",
            ",".join(["metric_time__day", *group_by]),
            "--csv",
            str(out),
        ],
        cwd=dbt_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    df = pd.read_csv(out, parse_dates=["metric_time__day"])
    return df.rename(columns={"metric_time__day": "date"})


@pytest.mark.parametrize("vertical", sorted(REGISTRY))
def test_every_dbt_metric_is_computed(vertical):
    """Nothing is silently left out: today every metric of every vertical is supported."""
    assert SemanticLayer.for_vertical(get_vertical(vertical)).unsupported() == {}


@pytest.mark.slow
@pytest.mark.skipif(not _available, reason="dbt extra / mf not installed")
@pytest.mark.parametrize("config_name", SMOKES)
def test_series_match_metricflow(config_name, tmp_path):
    cfg = load_config(ROOT / "configs" / f"{config_name}.yaml")
    vertical = get_vertical(cfg.company.vertical)
    result = generate(cfg)
    db = tmp_path / "world.duckdb"
    write_generation(result, db, sidecar_dir=tmp_path)

    dbt_dir = ROOT / "dbt" / vertical.dbt_project()
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir), "FAKE_DB": str(db)}
    build = subprocess.run(
        [str(DBT), "build", "--project-dir", str(dbt_dir)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-2000:]

    layer = SemanticLayer.for_vertical(vertical)
    names = sorted(layer.metrics)
    rate = next(a for a in cfg.anomalies.scripted if a.kind == "rate")
    root = vertical.root_label(rate.target, rate.segment)
    dims = tuple(root.slice)
    mine = layer.series(result.frames, result.tables, result.calendar, dimension_sets=[dims])
    lo, hi = pd.Timestamp(cfg.timeline.start), pd.Timestamp(cfg.timeline.end_date)

    # --- totals: every metric, every day ----------------------------------- #
    mf = _mf(dbt_dir, env, names, [], tmp_path / "totals.csv")
    mf = mf[(mf["date"] >= lo) & (mf["date"] <= hi)].set_index("date").sort_index()
    totals = mine[mine["dimension"].isna()].pivot(index="date", columns="metric", values="v")
    assert len(totals) == cfg.timeline.n_days, "the series must cover every timeline day"
    mismatched = []
    for name in names:
        theirs = mf[name].reindex(totals.index).to_numpy(dtype=float)
        ours = totals[name].to_numpy(dtype=float)
        ok = np.isclose(ours, theirs, rtol=1e-9, atol=1e-9, equal_nan=True)
        # A day MetricFlow leaves out entirely is a day with no rows for any
        # metric of the query: there, ours is the fill value or NULL.
        absent = ~totals.index.isin(mf.index)
        ok |= absent & (np.isnan(ours) | (ours == 0.0))
        if not ok.all():
            mismatched.append((name, int((~ok).sum())))
    assert not mismatched, f"{config_name}: series differ from MetricFlow: {mismatched}"

    # --- one slice: the smoke anomaly's root metric by its root dimensions -- #
    ids = [layer.dimensions(root.metric)[d] for d in dims]
    assert all(ids), (root, ids)
    sliced = _mf(dbt_dir, env, [root.metric], ids, tmp_path / "slice.csv")
    sliced = sliced[(sliced["date"] >= lo) & (sliced["date"] <= hi)]
    sliced["value"] = sliced[ids].astype(str).agg(",".join, axis=1)
    ours = mine[(mine["metric"] == root.metric) & (mine["dimension"] == ",".join(dims))]
    joined = sliced.merge(ours, on=["date", "value"], how="left")
    assert len(joined) == len(sliced) > 0
    assert np.allclose(
        joined["v"].to_numpy(dtype=float),
        joined[root.metric].to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-9,
        equal_nan=True,
    ), f"{config_name}: {root.metric} by {dims} differs from MetricFlow"
