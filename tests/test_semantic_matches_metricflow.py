"""The in-process metric series agree with MetricFlow (eval-worlds spec 4.5).

Measured effects are computed by ``fake_companies.semantic`` instead of
``dbt build`` + ``mf query`` per world. That is only honest if the two agree,
so for each vertical's smoke config this builds the dbt project, asks
MetricFlow for **every metric the semantic module computes** at day grain,
and requires the same numbers. It also checks every single-dimension slice
that goes into a world's ``series.parquet`` (one MetricFlow query per
dimension id), the two sides of every ratio metric, and one two-dimension
query: the root metric of the smoke anomaly, grouped by its root dimensions.
Distinct-count metrics are also checked at week grain (``metric_time__week``),
total and by each dimension, against the weekly series file's numbers.

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
    dbt_dir: Path,
    env: dict,
    metrics: list[str],
    group_by: list[str],
    out: Path,
    grain: str = "day",
) -> pd.DataFrame:
    proc = subprocess.run(
        [
            str(MF),
            "query",
            "--metrics",
            ",".join(metrics),
            "--group-by",
            ",".join([f"metric_time__{grain}", *group_by]),
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
    df = pd.read_csv(out, parse_dates=[f"metric_time__{grain}"])
    return df.rename(columns={f"metric_time__{grain}": "date"})


@pytest.mark.parametrize("vertical", sorted(REGISTRY))
def test_every_dbt_metric_is_computed(vertical):
    """Nothing is silently left out: today every metric of every vertical is supported."""
    assert SemanticLayer.for_vertical(get_vertical(vertical)).unsupported() == {}


def test_every_vertical_but_b2b_has_a_stock_tree_nodes_can_bind():
    """Retail and wholesale gained a stock each (eval-worlds spec 10.3); a
    tree there can now have a stock node. Both are running totals."""
    expect = {
        "b2c_saas": {"mrr", "active_subscriptions"},
        "b2b_services": None,  # unchanged: open pipeline and open deals
        "retail_dtc": {"customer_base"},
        "cpg_wholesale": {"trade_inventory_build"},
    }
    for name, stocks in expect.items():
        vertical = get_vertical(name)
        assert vertical.stock_metrics(), name
        if stocks is not None:
            assert stocks <= vertical.stock_metrics(), name
    for name, metric in [
        ("retail_dtc", "customer_base"),
        ("cpg_wholesale", "trade_inventory_build"),
    ]:
        layer = SemanticLayer.for_vertical(get_vertical(name))
        m = layer.metrics[metric]
        assert (m.kind, m.window_days, m.measure.agg) == ("cumulative", None, "sum")


def test_a_running_total_needs_an_additive_measure(tmp_path):
    import shutil

    import yaml

    project = tmp_path / "retail_dtc"
    shutil.copytree(ROOT / "dbt" / "retail_dtc" / "models", project / "models")
    path = project / "models" / "semantic" / "semantic_models.yml"
    sm = yaml.safe_load(path.read_text())
    customers = next(m for m in sm["semantic_models"] if m["name"] == "customers")
    customers["measures"][0]["agg"] = "count_distinct"
    customers["measures"][0]["expr"] = "customer_id"
    path.write_text(yaml.safe_dump(sm))
    layer = SemanticLayer(project)
    assert "count_distinct" in layer.unsupported()["customer_base"]


def test_new_retail_and_wholesale_metrics_from_the_rows():
    """discounted_orders, customer_base and trade_inventory_build recomputed
    from the raw frames, independently of the dbt SQL."""
    cfg = load_config(ROOT / "configs" / "smoke_retail_90d.yaml")
    result = generate(cfg, anomalies="none")  # no duplicate rows to dedupe
    layer = SemanticLayer.for_vertical(get_vertical(cfg.company.vertical))
    series = layer.series(
        result.frames,
        result.tables,
        result.calendar,
        metrics=["discounted_orders", "customer_base"],
    )
    totals = series[series["dimension"].isna()].pivot(index="date", columns="metric", values="v")
    orders = result.frames["shop_db.orders"]
    day = pd.to_datetime(orders["placed_at"]).dt.normalize()
    discounted = (orders["discount_amount"] > 0).groupby(day).sum()
    assert discounted.sum() > 0
    assert (totals["discounted_orders"].reindex(discounted.index) == discounted).all()
    customers = result.frames["shop_db.customers"]
    created = pd.to_datetime(customers["created_at"]).dt.normalize().value_counts().sort_index()
    base = created.cumsum().reindex(totals.index).ffill().fillna(0)
    assert (totals["customer_base"] == base).all()
    assert totals["customer_base"].is_monotonic_increasing

    cfg = load_config(ROOT / "configs" / "smoke_cpg_90d.yaml")
    result = generate(cfg, anomalies="none")  # no duplicate rows to dedupe
    layer = SemanticLayer.for_vertical(get_vertical(cfg.company.vertical))
    inv = layer.series(
        result.frames, result.tables, result.calendar, metrics=["trade_inventory_build"]
    )
    inv = inv[inv["dimension"].isna()].set_index("date")["v"]
    scans = result.frames["pos.scan_sales"]
    ships = result.frames["erp.shipments"].merge(
        result.frames["erp.products"][["product_id", "case_size"]], on="product_id"
    )
    weeks = pd.to_datetime(scans["week_start"])
    first, last = weeks.min(), weeks.max() + pd.Timedelta(days=6)
    shipped_day = pd.to_datetime(ships["shipped_at"]).dt.normalize()
    keep = shipped_day.between(first, last)
    sell_in = (ships["cases"] * ships["case_size"])[keep].groupby(shipped_day[keep]).sum()
    per_day = scans.groupby(weeks)["units"].sum() / 7
    sell_through = (
        pd.concat([per_day.rename(lambda d, k=k: d + pd.Timedelta(days=k)) for k in range(7)])
        .groupby(level=0)
        .sum()
    )
    flows = sell_in.reindex(inv.index, fill_value=0) - sell_through.reindex(inv.index, fill_value=0)
    assert np.allclose(inv.to_numpy(), flows.cumsum().to_numpy(), rtol=1e-9, atol=1e-6)


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
    singles = layer.single_dimension_sets()
    mine = layer.series(
        result.frames, result.tables, result.calendar, dimension_sets=[*singles, dims]
    )
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

    # --- ratio sides: numerator / denominator are the input metrics -------- #
    for name in names:
        metric = layer.metrics[name]
        rows = mine[(mine["metric"] == name) & mine["dimension"].isna()].set_index("date")
        if not layer.has_sides(name):
            assert rows["numerator"].isna().all() and rows["denominator"].isna().all(), name
            continue
        if metric.kind != "ratio":  # an average: its sum over its row count
            has = rows["denominator"] > 0
            assert np.allclose(
                rows.loc[has, "v"], rows.loc[has, "numerator"] / rows.loc[has, "denominator"]
            ), name
            assert rows.loc[~has, "v"].isna().all(), name
            continue
        for side, column in (("num", "numerator"), ("den", "denominator")):
            theirs = mf[metric.inputs[side]].reindex(rows.index).fillna(0.0).to_numpy(dtype=float)
            assert np.allclose(rows[column].to_numpy(dtype=float), theirs, rtol=1e-9), (name, side)
        defined = rows["denominator"] != 0
        assert np.allclose(
            rows.loc[defined, "v"],
            rows.loc[defined, "numerator"] / rows.loc[defined, "denominator"],
            rtol=1e-9,
            equal_nan=True,
        ), name

    # --- every single-dimension slice MetricFlow can address --------------- #
    by_id: dict[tuple[str, str], list[str]] = {}
    for name in names:
        for dim, mf_id in layer.dimensions(name).items():
            if mf_id:
                by_id.setdefault((dim, mf_id), []).append(name)
    assert by_id, "no sliceable metric?"
    checked = 0
    for i, ((dim, mf_id), group) in enumerate(sorted(by_id.items())):
        theirs = _mf(dbt_dir, env, group, [mf_id], tmp_path / f"dim{i}.csv")
        theirs = theirs[(theirs["date"] >= lo) & (theirs["date"] <= hi)]
        theirs["value"] = theirs[mf_id].map(_spell)
        for name in group:
            ours = mine[(mine["metric"] == name) & (mine["dimension"] == dim)]
            joined = theirs[["date", "value", name]].merge(ours, on=["date", "value"], how="left")
            # MetricFlow has a row per (day, value) that any metric of the
            # query has; ours is dense. Where MetricFlow shows NULL for this
            # metric, ours is the fill or NULL.
            a = joined["v"].to_numpy(dtype=float)
            b = joined[name].to_numpy(dtype=float)
            ok = np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)
            ok |= np.isnan(b) & (np.isnan(a) | (a == 0.0))
            assert ok.all(), f"{config_name}: {name} by {dim} differs on {int((~ok).sum())} rows"
            checked += 1
    assert checked >= len(by_id)

    # --- weekly distinct counts: MetricFlow's metric_time__week ------------ #
    weekly_names = layer.weekly_metrics()
    if not weekly_names:
        return
    con = layer.build_marts(result.frames, result.tables)
    try:
        weekly = layer.weekly_series_from(con, result.calendar, singles)
    finally:
        con.close()
    first_week = lo - pd.Timedelta(days=lo.dayofweek)
    groups: dict[tuple[str, ...], list[str]] = {(): weekly_names}
    for n in weekly_names:
        for dim, mf_id in layer.dimensions(n).items():
            if mf_id:
                groups.setdefault((dim, mf_id), []).append(n)
    for i, (key, group) in enumerate(groups.items()):
        dims, ids = key[:1], list(key[1:])
        theirs = _mf(dbt_dir, env, group, ids, tmp_path / f"week{i}.csv", grain="week")
        theirs = theirs[(theirs["date"] >= first_week) & (theirs["date"] <= hi)]
        theirs["value"] = theirs[ids[0]].map(_spell) if ids else None
        label = dims[0] if dims else None
        mine_w = weekly[
            weekly["dimension"].isna() if label is None else weekly["dimension"] == label
        ]
        for name in group:
            ours = mine_w[mine_w["metric"] == name][["date", "value", "v"]]
            on = ["date", "value"] if ids else ["date"]
            joined = theirs[[*on, name]].merge(ours, on=on, how="outer")
            a = joined["v"].to_numpy(dtype=float)
            b = joined[name].to_numpy(dtype=float)
            ok = np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)
            # a week (or slice value) MetricFlow has no row for: ours is the fill
            ok |= np.isnan(b) & ((a == 0.0) | np.isnan(a))
            assert ok.all(), (
                f"{config_name}: weekly {name} by {dims or 'total'} differs from MetricFlow "
                f"on {int((~ok).sum())} rows"
            )


def _spell(value) -> str:
    """A MetricFlow CSV dimension value as the series spell it."""
    if pd.isna(value):
        return "(null)"
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value)).lower()
    return str(value)
