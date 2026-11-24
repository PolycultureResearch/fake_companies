"""World sets (eval-worlds spec 4.3, 4.4, 4.7): build, manifest, stability."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from fake_companies.cli import app
from fake_companies.config import load_config
from fake_companies.effects import EFFECT_COLUMNS, NOISE_K
from fake_companies.generate import run_generation
from fake_companies.output.export import export_hashes
from fake_companies.worlds import (
    MANIFEST_NAME,
    WorldSetManifest,
    build_world_set,
    load_world_set,
    parse_seeds,
    plan_worlds,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RETAIL = REPO_ROOT / "configs" / "smoke_retail_90d.yaml"
SMOKES = ["smoke_90d", "smoke_retail_90d", "smoke_b2b_90d", "smoke_cpg_90d"]


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def retail_set(tmp_path_factory) -> tuple[Path, WorldSetManifest]:
    """3 seeds x (null + both singles + story), Parquet, built with 2 jobs."""
    out = tmp_path_factory.mktemp("worlds") / "retail"
    manifest = build_world_set(
        RETAIL, out, seeds=[0, 1, 2], null=True, singles="all", story=True, fmt="parquet", jobs=2
    )
    return out, manifest


def test_old_and_new_manifests_are_refused_up_front(retail_set, tmp_path):
    from fake_companies.worlds import SCHEMA_VERSION, UnsupportedWorldSet

    out, _ = retail_set
    assert load_world_set(out, min_version=2).schema_version == SCHEMA_VERSION == 2
    data = json.loads((out / MANIFEST_NAME).read_text())
    for version in (1, 3):  # older than asked for; newer than this library
        path = tmp_path / f"v{version}.json"
        path.write_text(json.dumps({**data, "schema_version": version}))
        with pytest.raises(UnsupportedWorldSet, match=f"schema_version {version}"):
            load_world_set(path, min_version=2)
    # a version 1 manifest still reads when the reader does not ask for 2
    v1 = {**data, "schema_version": 1, "verdicts": []}
    (tmp_path / "v1ok.json").write_text(json.dumps(v1))
    assert load_world_set(tmp_path / "v1ok.json").schema_version == 1


def test_set_summary_says_where_twins_were_aligned(retail_set):
    """One flag per (anomaly, metric, slice) instead of every twin's summary."""
    out, manifest = retail_set
    summary = pd.read_parquet(out / manifest.effects_summary)
    keys = ["anomaly", "metric", "dimension", "value"]
    twins = []
    for single, _ in manifest.twins():
        t = pd.read_parquet(out / single.effects_summary)
        t["anomaly"] = json.loads((out / single.ground_truth).read_text())[0]["id"]
        twins.append(t)
    per_twin = pd.concat(twins)
    judged = per_twin["pre_onset_days"] > 0
    per_twin["differed"] = judged & (per_twin["pre_onset_max_abs_diff"] != 0)
    per_twin["aligned"] = judged & ~per_twin["differed"]
    for frame in (per_twin, summary):
        frame[["dimension", "value"]] = frame[["dimension", "value"]].fillna("")
    g = per_twin.groupby(keys)
    expect = pd.DataFrame({"any_differed": g["differed"].any(), "all_aligned": g["aligned"].all()})
    got = summary.set_index(keys)["aligned_before_start"].reindex(expect.index)
    assert (got[expect["any_differed"]] == False).all()
    assert (got[expect["all_aligned"]] == True).all()
    assert got[~expect["any_differed"] & ~expect["all_aligned"]].isna().all()
    assert (got == True).any() and (got == False).any()


def _recipe_window_change(null, world, info, a, b):
    """Spec 10.7, written out independently of fake_companies.effects:
    (rel_change, rel_change_aggregated) of one total over days a..b."""
    import math

    def rows(frame, name):
        r = frame[(frame["metric"] == name) & frame["dimension"].isna()]
        d = pd.to_datetime(r["date"])
        return r[(d >= a) & (d <= b)].sort_values("date")

    def agg(frame, name):
        m = info[name]
        r = rows(frame, name)
        if m.has_sides:
            rules = (
                tuple(m.input_time_aggregation[k] for k in ("numerator", "denominator"))
                if m.type == "ratio"
                else ("sum", "sum")
            )
            num, den = (side(r[c], rule) for c, rule in zip(("numerator", "denominator"), rules))
            return num / den if den else math.nan
        if m.time_aggregation == "derived":
            return formula(m.formula, {k: agg(frame, k) for k in m.inputs.values()})
        return side(r["metric_value"], m.time_aggregation)

    def side(values, rule):
        v = values.dropna()
        if v.empty:
            return math.nan
        if rule == "sum":
            return float(v.sum())
        if rule in ("end_of_period", "rolling_window"):
            return float(v.iloc[-1])
        return math.nan

    def formula(node, values):
        if "const" in node:
            return node["const"]
        if "metric" in node:
            return values[node["metric"]]
        x = [formula(n, values) for n in node["args"]]
        op = node["op"]
        if op == "neg":
            return -x[0]
        if op == "nullif_zero":
            return math.nan if x[0] == 0 else x[0]
        if op == "/":
            return math.nan if not x[1] or math.isnan(x[1]) else x[0] / x[1]
        return {"+": x[0] + x[1], "-": x[0] - x[1], "*": x[0] * x[1]}[op]

    def rel(w, n):
        return math.nan if not n or math.isnan(n) or math.isnan(w) else w / n - 1

    out = {}
    for name in info:
        n, w = rows(null, name)["metric_value"].mean(), rows(world, name)["metric_value"].mean()
        out[name] = (rel(w, n), rel(agg(world, name), agg(null, name)))
    return out


def test_any_window_can_be_measured_from_the_series_as_the_spec_says(retail_set):
    """The recipe in spec 10.7 reproduces the set's own numbers on the
    declared window, so a consumer measuring another window from the series
    files computes what the generator would have."""
    out, manifest = retail_set
    info = {m.name: m for m in manifest.metrics}
    checked = 0
    for single, null in manifest.twins():
        truth = json.loads((out / single.ground_truth).read_text())[0]
        a = pd.Timestamp(truth["start"])
        b = pd.Timestamp(truth["end"]) if truth["end"] else pd.Timestamp("2100-01-01")
        mine = _recipe_window_change(
            pd.read_parquet(out / null.series), pd.read_parquet(out / single.series), info, a, b
        )
        theirs = pd.read_parquet(out / single.effects_summary)
        theirs = theirs[theirs["dimension"].isna()].set_index("metric")
        for name, (rel, rel_agg) in mine.items():
            for got, want in ((rel, "rel_change"), (rel_agg, "rel_change_aggregated")):
                expected = theirs.loc[name, want]
                assert np.isclose(got, expected, rtol=1e-9, equal_nan=True), (name, want)
                checked += 1
    assert checked > 100
    # a rate's two effects differ: the mean of daily ratios is not Σnum / Σden
    summary = pd.read_parquet(out / manifest.effects_summary)
    rates = summary[
        summary["dimension"].isna() & summary["metric"].isin(["conversion_rate", "aov"])
    ]
    assert rates["rel_change_aggregated_mean"].notna().all()
    assert not np.allclose(rates["rel_change_mean"], rates["rel_change_aggregated_mean"])


def test_parse_seeds():
    assert parse_seeds("0-3") == [0, 1, 2, 3]
    assert parse_seeds("7") == [7]
    assert parse_seeds("0-2, 7,1") == [0, 1, 2, 7]
    for bad in ("a", "3-1", "1-", ""):
        with pytest.raises(ValueError, match="seeds"):
            parse_seeds(bad)


def test_plan_orders_worlds_and_singles_imply_the_null():
    plans = plan_worlds("co", ["baseline", "flat"], [0, 1], null=False, singles=["a"], story=True)
    assert [p.id for p in plans[:3]] == [
        "co.baseline.s0.null",
        "co.baseline.s0.only.a",
        "co.baseline.s0.story",
    ]
    assert len(plans) == 12
    single = plans[1]
    assert (single.kind, single.selection, single.twin_of) == ("single", "only:a", plans[0].id)
    assert plan_worlds("co", ["baseline"], [0], null=False, singles=[], story=False) == []


def test_manifest_is_the_contract(retail_set):
    out, manifest = retail_set
    loaded = load_world_set(out)
    assert loaded == manifest == load_world_set(out / MANIFEST_NAME)
    assert loaded.schema_version == 2
    assert loaded.vertical == "retail_dtc" and loaded.format == "parquet"
    assert loaded.config_sha256 == hashlib.sha256(RETAIL.read_bytes()).hexdigest()
    assert loaded.seeds == [0, 1, 2] and loaded.regimes == ["baseline"]

    kinds = [w.kind for w in loaded.worlds]
    assert kinds.count("null") == 3 and kinds.count("single") == 6 and kinds.count("story") == 3
    ids = [w.id for w in loaded.worlds]
    assert len(set(ids)) == len(ids)
    assert "retail_smoke.baseline.s1.only.mobile_checkout_regression" in ids

    for w in loaded.worlds:
        assert (out / w.path).is_dir()
        truth = json.loads((out / w.ground_truth).read_text())
        assert [r["id"] for r in truth] == w.anomalies
        run = json.loads((out / w.run_manifest).read_text())
        assert run["seed"] == str(w.seed) and run["config_hash"] == w.config_hash
        assert run["regime"] == w.regime
        if w.kind == "null":
            assert w.anomalies == [] and w.twin_of is None and w.effects is None
            assert run["anomaly_selection"] == "none"
        elif w.kind == "single":
            assert len(w.anomalies) == 1
            assert loaded.world(w.twin_of).kind == "null"
            assert loaded.world(w.twin_of).seed == w.seed
            assert (out / w.effects).is_file()
        else:
            assert len(w.anomalies) == 2 and w.effects is None
            assert "anomaly_selection" not in run
    assert len(loaded.twins()) == 6


def test_manifest_describes_the_metrics(retail_set):
    _, manifest = retail_set
    by_name = {m.name: m for m in manifest.metrics}
    assert manifest.unsupported_metrics == {}
    assert by_name["conversion_rate"].type == "ratio"
    assert by_name["conversion_rate"].dimensions["channel"] == "session__channel"
    assert by_name["orders"].fill_nulls_with == 0.0
    # Sliceable here, but not by one MetricFlow group-by (two semantic models).
    assert by_name["return_rate"].dimensions == {"category": None}
    assert manifest.noise.k == NOISE_K
    # How each metric aggregates over time, for a consumer resampling to weeks.
    assert by_name["orders"].time_aggregation == "sum"
    assert by_name["conversion_rate"].time_aggregation == "ratio_of_sums"
    assert by_name["conversion_rate"].inputs == {
        "numerator": "converting_sessions",
        "denominator": "sessions",
    }
    assert by_name["net_revenue"].time_aggregation == "derived"
    assert by_name["net_revenue"].expr == "gross_revenue - discounts - refunds"
    assert set(by_name["net_revenue"].inputs.values()) == {"gross_revenue", "discounts", "refunds"}
    assert by_name["avg_fulfillment_days"].time_aggregation == "average"


def test_stocks_and_windows_are_marked_in_the_manifest(tmp_path):
    manifest = build_world_set(
        REPO_ROOT / "configs" / "smoke_90d.yaml", tmp_path, seeds=[0], null=True, raw=False
    )
    agg = {m.name: m.time_aggregation for m in manifest.metrics}
    assert agg["mrr"] == agg["active_subscriptions"] == "end_of_period"
    assert agg["new_mrr"] == "sum" and agg["dau"] == "count_distinct"
    assert agg["wau"] == "rolling_window" and agg["arpu"] == "derived"
    # what MetricFlow answers at week grain, and where the rule differs from it
    # (tests/test_semantic_matches_metricflow.py checks both against mf query)
    mfw = {m.name: (m.metricflow_week, m.matches_metricflow_week) for m in manifest.metrics}
    # Stocks report their last day to MetricFlow too (semi-additive snapshot
    # measure; period_agg: last on a cumulative metric), spec §10.10.
    assert mfw["mrr"] == ("last_day", True)
    assert mfw["wau"] == ("last_day", True)
    assert mfw["dau"] == ("distinct_over_week", False)
    assert mfw["new_mrr"] == ("sum_of_days", True)
    assert mfw["visit_signup_rate"] == ("ratio_of_week_inputs", True)
    assert mfw["customer_churn_rate"] == ("ratio_of_week_inputs", False)
    assert mfw["arpu"] == ("derived_of_week_inputs", False)
    churn = next(m for m in manifest.metrics if m.name == "customer_churn_rate")
    # the business rule takes the last day's active subscriptions; MetricFlow
    # takes the week's distinct ones, which the weekly series file serves
    assert churn.input_time_aggregation["denominator"] == "end_of_period"
    assert churn.input_metricflow_week == {
        "numerator": "sum_of_days",
        "denominator": "distinct_over_week",
    }
    world = manifest.worlds[0]
    assert world.path is None and world.series == "baseline/s0/null.series.parquet"
    files = sorted(p.name for p in (tmp_path / "baseline" / "s0").iterdir())
    assert files == [
        "null.ground_truth.json",
        "null.run_manifest.json",
        "null.series.parquet",
        "null.weekly_series.parquet",
    ]


def test_distinct_counts_have_a_weekly_series(tmp_path):
    """DAU summed over a week is not the week's distinct users, and active
    subscriptions on Sunday are not the subscriptions active during the week:
    those weekly numbers come from their own file (MetricFlow's
    metric_time__week; tests/test_semantic_matches_metricflow.py holds it to
    mf query)."""
    manifest = build_world_set(
        REPO_ROOT / "configs" / "smoke_90d.yaml", tmp_path, seeds=[0], null=True, raw=False
    )
    weekly_names = {m.name for m in manifest.metrics if m.has_weekly_series}
    assert weekly_names == {"active_subscriptions", "dau", "paying_active_users"}
    world = manifest.worlds[0]
    assert world.weekly_series == "baseline/s0/null.weekly_series.parquet"
    weekly = pd.read_parquet(tmp_path / world.weekly_series)
    assert list(weekly.columns) == ["date", "metric", "dimension", "value", "metric_value", "days"]
    weekly["date"] = pd.to_datetime(weekly["date"])
    assert (weekly["date"].dt.dayofweek == 0).all(), "weeks start on Monday, as MetricFlow's"
    totals = weekly[weekly["dimension"].isna()]
    assert set(totals["metric"]) == weekly_names
    by_name = {m.name: m for m in manifest.metrics}
    listed = {(n, d) for n in weekly_names for d in by_name[n].dimensions}
    sliced = weekly[weekly["dimension"].notna()]
    assert set(zip(sliced["metric"], sliced["dimension"])) == listed
    # every timeline day falls in exactly one week
    per_week = totals[totals["metric"] == "dau"].set_index("date")
    assert per_week["days"].sum() == 90 and per_week["days"].between(1, 7).all()

    daily = pd.read_parquet(tmp_path / world.series)
    daily = daily[daily["dimension"].isna()].copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["week"] = daily["date"] - pd.to_timedelta(daily["date"].dt.dayofweek, unit="D")
    for name in weekly_names:
        days = daily[daily["metric"] == name].groupby("week")["metric_value"]
        week = totals[totals["metric"] == name].set_index("date")["metric_value"]
        # distinct over the week: at least the busiest day, at most the days added up
        assert (week >= days.max().reindex(week.index)).all(), name
        assert (week <= days.sum().reindex(week.index)).all(), name
    dau = totals[totals["metric"] == "dau"].set_index("date")["metric_value"]
    assert (dau < daily[daily["metric"] == "dau"].groupby("week")["metric_value"].sum()).any()


def test_every_world_has_a_series_file(retail_set):
    out, manifest = retail_set
    by_name = {m.name: m for m in manifest.metrics}
    days = 90
    for world in manifest.worlds:
        series = pd.read_parquet(out / world.series)
        assert list(series.columns) == [
            "date",
            "metric",
            "dimension",
            "value",
            "metric_value",
            "numerator",
            "denominator",
        ]
        totals = series[series["dimension"].isna()]
        assert set(totals["metric"]) == set(by_name), "a total for every metric"
        assert (totals.groupby("metric").size() == days).all(), "every day of the timeline"
        assert totals["value"].isna().all()
        # every single-dimension slice the manifest lists, and nothing else
        listed = {(m.name, d) for m in manifest.metrics for d in m.dimensions}
        sliced = series[series["dimension"].notna()]
        assert set(zip(sliced["metric"], sliced["dimension"])) == listed
        assert not sliced["dimension"].str.contains(",").any()
        sided = series["metric"].map(lambda m: by_name[m].has_sides)
        assert series.loc[sided, ["numerator", "denominator"]].notna().all().all()
        assert series.loc[~sided, ["numerator", "denominator"]].isna().all().all()
    assert {m.name for m in manifest.metrics if m.has_sides} == {
        *(m.name for m in manifest.metrics if m.type == "ratio"),
        "avg_fulfillment_days",
    }


def test_series_are_the_values_behind_the_effects(retail_set):
    """For a twin, the two series files hold exactly the effects table's
    ``null_value`` and ``world_value``."""
    out, manifest = retail_set
    keys = ["date", "metric", "dimension", "value"]
    for world, null in manifest.twins():
        effects = _keyed(pd.read_parquet(out / world.effects))
        effects = effects[~effects["dimension"].str.contains(",")]
        for column, entry in (("null_value", null), ("world_value", world)):
            series = _keyed(pd.read_parquet(out / entry.series))[[*keys, "metric_value"]]
            joined = effects.merge(series, on=keys, how="left", indicator=True)
            assert (joined["_merge"] == "both").all()
            a, b = (
                joined[column].to_numpy(dtype=float),
                joined["metric_value"].to_numpy(dtype=float),
            )
            assert ((a == b) | (np.isnan(a) & np.isnan(b))).all(), (world.id, column)


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    """Null slice keys as "" so totals join (an all-null column reads as float)."""
    frame = frame.copy()
    for column in ("dimension", "value"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), "")
    return frame


def test_weekly_ratio_from_the_series_sides(retail_set):
    """The point of numerator/denominator: a weekly conversion rate is the
    ratio of weekly sums, which the daily ratios alone cannot give."""
    out, manifest = retail_set
    series = pd.read_parquet(out / manifest.worlds[0].series)
    rate = series[(series["metric"] == "conversion_rate") & series["dimension"].isna()]
    parts = series[series["dimension"].isna()].pivot(
        index="date", columns="metric", values="metric_value"
    )
    week = pd.to_datetime(rate["date"]).dt.to_period("W").to_numpy()
    from_sides = rate.groupby(week)["numerator"].sum() / rate.groupby(week)["denominator"].sum()
    truth = parts["converting_sessions"].groupby(week).sum() / parts["sessions"].groupby(week).sum()
    assert np.allclose(from_sides.to_numpy(), truth.to_numpy())
    assert not np.allclose(
        from_sides.to_numpy(), rate.groupby(week)["metric_value"].mean().to_numpy()
    )


def test_weekly_average_from_the_series_sides(retail_set):
    """An average carries its sum and row count: the weekly mean is the sum
    of sums over the sum of counts, exactly, and not the mean of daily means."""
    out, manifest = retail_set
    null = manifest.worlds[0]
    series = pd.read_parquet(out / null.series)
    avg = series[(series["metric"] == "avg_fulfillment_days") & series["dimension"].isna()]
    defined = avg["denominator"] > 0
    assert np.allclose(
        avg.loc[defined, "metric_value"],
        avg.loc[defined, "numerator"] / avg.loc[defined, "denominator"],
    )
    orders = pd.read_parquet(out / null.path / "shop_db.orders.parquet")
    ship = pd.read_parquet(out / null.path / "fulfillment.shipments.parquet")
    joined = orders.merge(ship[["order_id", "delivered_at"]], on="order_id")
    days = (
        pd.to_datetime(joined["delivered_at"]).dt.normalize()
        - pd.to_datetime(joined["placed_at"]).dt.normalize()
    ).dt.days
    week = pd.to_datetime(joined["placed_at"]).dt.to_period("W")
    truth = days.groupby(week.to_numpy()).mean().dropna()
    wk = pd.to_datetime(avg["date"]).dt.to_period("W").to_numpy()
    ours = (avg.groupby(wk)["numerator"].sum() / avg.groupby(wk)["denominator"].sum()).dropna()
    assert np.allclose(ours.to_numpy(), truth.reindex(ours.index).to_numpy())


def test_manifest_says_how_each_side_of_a_ratio_aggregates(tmp_path):
    """customer_churn_rate divides a flow by a stock: summing both sides over
    a week is wrong by about a factor of seven, and the manifest says so."""
    manifest = build_world_set(
        REPO_ROOT / "configs" / "smoke_90d.yaml", tmp_path, seeds=[0], null=True, raw=False
    )
    by_name = {m.name: m for m in manifest.metrics}
    churn = by_name["customer_churn_rate"]
    assert churn.time_aggregation == "ratio_of_aggregates"
    assert churn.inputs == {
        "numerator": "churned_subscriptions",
        "denominator": "active_subscriptions",
    }
    assert churn.input_time_aggregation == {"numerator": "sum", "denominator": "end_of_period"}
    rate = by_name["visit_signup_rate"]
    assert rate.time_aggregation == "ratio_of_sums"
    assert rate.input_time_aggregation == {"numerator": "sum", "denominator": "sum"}
    # a stock that is a distinct count, and says so
    active = by_name["active_subscriptions"]
    assert (active.time_aggregation, active.measure_agg) == ("end_of_period", "count_distinct")
    assert by_name["mrr"].measure_agg == "sum" and churn.measure_agg is None
    # derived metrics: a tree, not SQL to parse
    arpu = by_name["arpu"]
    assert arpu.formula == {
        "op": "/",
        "args": [
            {"metric": "mrr"},
            {"op": "nullif_zero", "args": [{"metric": "active_subscriptions"}]},
        ],
    }
    assert arpu.input_time_aggregation == {
        "mrr": "end_of_period",
        "active_subscriptions": "end_of_period",
    }
    assert by_name["mrr"].formula is None


def test_formula_parser():
    from fake_companies.semantic import _parse_formula

    names = {"a": "m_a", "b": "m_b"}
    assert _parse_formula("a - b * 2", names) == {
        "op": "-",
        "args": [{"metric": "m_a"}, {"op": "*", "args": [{"metric": "m_b"}, {"const": 2.0}]}],
    }
    assert _parse_formula("-(a)", names) == {"op": "neg", "args": [{"metric": "m_a"}]}
    for bad in ("a + c", "coalesce(a, 0)", "nullif(a, 1)", "a +", "a b"):
        with pytest.raises(ValueError):
            _parse_formula(bad, names)


def test_a_twin_has_a_summary_row_for_every_slice(retail_set):
    """A node bound to a slice (paid sessions = sessions where channel is
    paid_search) looks its measured effect up in the twin's summary table."""
    out, manifest = retail_set
    world = manifest.world("retail_smoke.baseline.s0.only.mobile_checkout_regression")
    summary = pd.read_parquet(out / world.effects_summary)
    series = pd.read_parquet(out / world.series)
    keys = ["metric", "dimension", "value"]
    have = {tuple(x) for x in _keyed(summary)[keys].itertuples(index=False)}
    want = {tuple(x) for x in _keyed(series)[keys].drop_duplicates().itertuples(index=False)}
    assert want <= have, "every series row has measured stats"
    row = summary[
        (summary["metric"] == "converting_sessions")
        & (summary["dimension"] == "channel")
        & (summary["value"] == "paid_search")
    ].iloc[0]
    assert row["moved"] and row["rel_change"] < -0.25
    untouched = summary[
        (summary["metric"] == "converting_sessions")
        & (summary["dimension"] == "channel")
        & (summary["value"] == "organic")
    ].iloc[0]
    assert not untouched["differs"] and not untouched["moved"]
    truth = json.loads((out / world.ground_truth).read_text())[0]
    totals = summary[summary["dimension"].isna()].set_index("metric")
    for metric, stats in truth["measured_effects"].items():
        assert bool(totals.loc[metric, "moved"]) == stats["moved"]


def test_set_summary_has_a_verdict_per_anomaly_and_metric(retail_set):
    out, manifest = retail_set
    summary = pd.read_parquet(out / manifest.effects_summary)
    assert set(summary["verdict"]) <= {
        "established_up",
        "established_down",
        "open",
        "exactly_zero",
        "undefined",
    }
    assert set(summary["verdict_10pct"]) <= set(summary["verdict"]) | {
        "established_up",
        "established_down",
    }
    # the loose verdict never contradicts the strict one; it can only settle more
    strict = summary["verdict"].str.startswith("established")
    assert (summary.loc[strict, "verdict_10pct"] == summary.loc[strict, "verdict"]).all()
    assert [v.column for v in manifest.verdicts] == ["verdict", "verdict_10pct"]
    strict_def, loose_def = manifest.verdicts
    assert (strict_def.level, loose_def.level) == (0.01, 0.10)
    assert strict_def.t_critical == pytest.approx(9.925)  # three seeds
    assert loose_def.t_critical == pytest.approx(2.920)
    assert strict_def.min_seeds == loose_def.min_seeds == 2
    for v in manifest.verdicts:
        col = summary[v.seeds_to_establish_column]
        # an integer column (pandas reads an integer column with nulls as float)
        assert duckdb.sql(
            f"SELECT typeof({v.seeds_to_establish_column}) AS t "
            f"FROM '{out / manifest.effects_summary}' LIMIT 1"
        ).fetchone()[0] in ("BIGINT", "INTEGER")
        settled = summary[v.column].str.startswith("established")
        assert (col[settled] <= len(manifest.seeds)).all()
        assert col[summary[v.column] == "exactly_zero"].isna().all()
    rows = summary[summary["anomaly"] == "mobile_checkout_regression"]
    total = rows[rows["dimension"].isna()].set_index("metric")
    assert total.loc["sessions", "verdict"] == "exactly_zero"  # upstream of the anomaly
    assert total.loc["marketing_spend", "verdict"] == "exactly_zero"
    root = rows[(rows["metric"] == "conversion_rate") & (rows["dimension"] == "channel,device")]
    root = root[root["value"] == "paid_search,mobile"].iloc[0]
    assert root["rel_change_mean"] < -0.5 and root["moved_share"] == 1.0


def test_no_raw_and_no_series(tmp_path):
    a = build_world_set(RETAIL, tmp_path / "a", seeds=[0], singles="all", raw=False, jobs=1)
    assert all(w.path is None and w.series for w in a.worlds)
    assert not list((tmp_path / "a").rglob("*.duckdb"))
    assert all((tmp_path / "a" / w.effects).is_file() for w in a.worlds if w.kind == "single")
    b = build_world_set(RETAIL, tmp_path / "b", seeds=[0], null=True, series=False, effects=False)
    assert b.worlds[0].series is None and b.worlds[0].path == "baseline/s0/null.duckdb"
    with pytest.raises(ValueError, match="no data"):
        build_world_set(
            RETAIL, tmp_path / "c", seeds=[0], null=True, raw=False, series=False, effects=False
        )


def test_parquet_world_holds_the_raw_tables(retail_set):
    out, manifest = retail_set
    world = manifest.world("retail_smoke.baseline.s0.null")
    files = sorted(p.name for p in (out / world.path).iterdir())
    assert files == sorted(
        f"{t}.parquet"
        for t in (
            "ad_platform.ad_spend",
            "web.sessions",
            "shop_db.products",
            "shop_db.customers",
            "shop_db.orders",
            "shop_db.order_items",
            "fulfillment.shipments",
            "shop_db.returns",
            "payments.transactions",
        )
    )
    orders = pd.read_parquet(out / world.path / "shop_db.orders.parquet")
    assert len(orders) > 0 and orders["_loaded_at"].notna().all()


def test_ids_and_bytes_are_stable_across_runs_and_jobs(retail_set, tmp_path):
    """Same config, seed, regime and anomaly -> same id and the same bytes,
    whatever else is in the set and however many processes built it."""
    out, manifest = retail_set
    again = tmp_path / "again"
    rebuilt = build_world_set(
        RETAIL, again, seeds=[0, 1, 2], null=True, singles="all", story=True, fmt="parquet", jobs=1
    )
    assert rebuilt == manifest
    assert _file_hashes(again) == _file_hashes(out)

    # A different selection of worlds: the ones in common are byte-identical.
    subset = tmp_path / "subset"
    small = build_world_set(
        RETAIL, subset, seeds=[1], singles="mobile_checkout_regression", fmt="parquet", jobs=3
    )
    assert [w.id for w in small.worlds] == [
        "retail_smoke.baseline.s1.null",
        "retail_smoke.baseline.s1.only.mobile_checkout_regression",
    ]
    full, part = _file_hashes(out), _file_hashes(subset)
    shared = set(part) - {MANIFEST_NAME, "effects_summary.parquet"}
    assert shared and all(part[name] == full[name] for name in shared)
    assert small.world(small.worlds[1].id) == manifest.world(small.worlds[1].id)


@pytest.mark.parametrize("name", SMOKES)
def test_duckdb_worlds_have_stable_content(name, tmp_path):
    """DuckDB files are compared by content (Parquet export hashes): the
    database file itself is not promised to be byte-stable."""
    config = REPO_ROOT / "configs" / f"{name}.yaml"
    a = build_world_set(config, tmp_path / "a", seeds=[3], null=True, story=True, jobs=1)
    b = build_world_set(config, tmp_path / "b", seeds=[3], null=True, story=True, jobs=1)
    assert a == b and a.format == "duckdb"
    for w in a.worlds:
        ha = export_hashes(tmp_path / "a" / w.path, tmp_path / "ea" / w.kind)
        hb = export_hashes(tmp_path / "b" / w.path, tmp_path / "eb" / w.kind)
        assert ha == hb
    null = a.world(f"{a.company_slug}.baseline.s3.null")
    con = duckdb.connect(str(tmp_path / "a" / null.path), read_only=True)
    assert con.execute("SELECT count(*) FROM meta.ground_truth").fetchone() == (0,)
    con.close()


def test_story_world_is_the_default_generation(retail_set, tmp_path):
    """The story world at the config's own seed is exactly what `generate` writes."""
    cfg = load_config(RETAIL)
    out = tmp_path / "set"
    manifest = build_world_set(RETAIL, out, seeds=[cfg.seed], story=True)
    story = manifest.worlds[0]
    assert story.kind == "story"
    plain = tmp_path / "plain.duckdb"
    run_generation(cfg, plain, sidecar_dir=tmp_path / "plain")
    ours = export_hashes(out / story.path, tmp_path / "e1")
    theirs = export_hashes(plain, tmp_path / "e2")
    ours.pop("meta.run_manifest.parquet")  # the world's manifest also records its regime
    theirs.pop("meta.run_manifest.parquet")
    assert ours == theirs


def test_regimes_and_overrides_are_recorded(tmp_path):
    manifest = build_world_set(
        RETAIL,
        tmp_path,
        seeds=[0],
        regimes=["baseline", "flat"],
        null=True,
        overrides={"noise.ar1": 0.1},
        effects=False,
        series=False,
    )
    base, flat = manifest.worlds
    assert (base.regime, flat.regime) == ("baseline", "flat")
    assert base.overrides == {"noise.ar1": 0.1}
    assert flat.overrides["traffic.channels.*.growth"] == {"kind": "flat"}
    assert flat.overrides["noise.ar1"] == 0.1
    assert base.config_hash != flat.config_hash
    assert base.path == "baseline/s0/null.duckdb" and flat.path == "flat/s0/null.duckdb"
    assert manifest.metrics == [] and manifest.effects_summary is None


def test_bad_requests_are_refused(tmp_path):
    with pytest.raises(ValueError, match="nothing to build"):
        build_world_set(RETAIL, tmp_path, seeds=[0])
    with pytest.raises(ValueError, match="unknown regime"):
        build_world_set(RETAIL, tmp_path, seeds=[0], regimes=["sideways"], null=True)
    with pytest.raises(ValueError, match="unknown scripted anomalies"):
        build_world_set(RETAIL, tmp_path, seeds=[0], singles="nope")
    with pytest.raises(ValueError, match="format"):
        build_world_set(RETAIL, tmp_path, seeds=[0], null=True, fmt="csv")


def test_cli_builds_a_set_quickly(tmp_path):
    """Spec 4.7: 3 seeds x (null + singles) of a smoke config, well under a minute."""
    out = tmp_path / "cli"
    t0 = time.perf_counter()
    result = CliRunner().invoke(
        app,
        ["worlds", "-c", str(RETAIL), "-o", str(out), "--seeds", "0-2", "--null"]
        + ["--singles", "all", "--format", "parquet"],
    )
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert "Built 9 worlds (3 null, 6 single, 0 story)" in result.output
    assert elapsed < 40, f"world set took {elapsed:.1f}s"
    manifest = load_world_set(out)
    effects = pd.read_parquet(out / manifest.twins()[0][0].effects)
    assert list(effects.columns) == EFFECT_COLUMNS
    assert (out / manifest.effects_summary).is_file()


def test_cli_generate_selection_and_set(tmp_path):
    db = tmp_path / "x.duckdb"
    result = CliRunner().invoke(
        app,
        ["generate", "-c", str(RETAIL), "-o", str(db), "--anomalies", "none"]
        + ["--set", "timeline.days=100", "--regime", "no_seasonality"],
    )
    assert result.exit_code == 0, result.output
    assert "0 ground-truth events" in result.output
    run = json.loads((tmp_path / "run_manifest.json").read_text())
    assert run["anomaly_selection"] == "none" and run["regime"] == "no_seasonality"
    assert json.loads(run["overrides"])["timeline.days"] == 100
    bad = CliRunner().invoke(
        app, ["generate", "-c", str(RETAIL), "-o", str(db), "--anomalies", "only:nope"]
    )
    assert bad.exit_code != 0
