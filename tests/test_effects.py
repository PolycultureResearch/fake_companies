"""Measured effects and twin coupling (eval-worlds spec 4.5, 4.7).

Two kinds of test:

- unit tests of the effects arithmetic on synthetic series;
- for each vertical's smoke config, a three-seed world set whose twins are
  checked against what the generator declares (sign and size of the root
  effect) and against what is true of the random streams (which tables and
  metrics a single world shares with its null twin before the anomaly, and
  which it does not).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.corruption.loading import event_reference
from fake_companies.effects import (
    NOISE_K,
    ground_truth_fields,
    summarize_across_seeds,
    summarize_twin,
    twin_effects,
)
from fake_companies.generate import generate
from fake_companies.verticals import get_vertical
from fake_companies.worlds import build_world_set

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKES = ["smoke_90d", "smoke_retail_90d", "smoke_b2b_90d", "smoke_cpg_90d"]
SEEDS = [0, 1, 2]


# --------------------------------------------------------------------------- #
# The arithmetic, on synthetic series
# --------------------------------------------------------------------------- #
DAYS = pd.date_range("2024-01-01", periods=120, freq="D")
START, END = dt.date(2024, 2, 20), dt.date(2024, 3, 10)


def _series(values, metric="m", dimension=None, value=None) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": DAYS, "metric": metric, "dimension": dimension, "value": value, "v": values}
    )


def _window() -> np.ndarray:
    return np.asarray((DAYS >= pd.Timestamp(START)) & (DAYS <= pd.Timestamp(END)))


def _summary(null, world) -> pd.Series:
    effects = twin_effects(_series(null), _series(world), {"m": 0.0})
    return summarize_twin(effects, START, END).iloc[0]


def test_a_step_is_measured_against_diverged_noise():
    rng = np.random.default_rng(0)
    null = 100 + rng.normal(0, 3, len(DAYS))
    world = null + rng.normal(0, 3, len(DAYS))  # realization noise from day one
    world[_window()] -= 30
    row = _summary(null, world)
    assert row["moved"] and not row["stock_like"]
    assert row["rel_change"] == pytest.approx(-0.30, abs=0.03)
    assert row["z_window"] < -NOISE_K
    assert row["pre_onset_rms_diff"] == pytest.approx(3.0, rel=0.3)
    assert row["onset"] == START and row["offset"] == END
    assert row["days_over_floor"] == _window().sum()


def test_noise_alone_does_not_move():
    rng = np.random.default_rng(1)
    null = 100 + rng.normal(0, 3, len(DAYS))
    row = _summary(null, null + rng.normal(0, 3, len(DAYS)))
    assert not row["moved"]
    assert abs(row["z_window"]) < NOISE_K and abs(row["z_extended"]) < NOISE_K
    assert row["days_over_floor"] <= 1


def test_aligned_streams_have_zero_pre_onset_noise():
    """Twins identical until the anomaly: the floor comes from the step
    estimate over the diverged days, not from the (zero) pre-onset noise."""
    rng = np.random.default_rng(2)
    null = 100 + rng.normal(0, 3, len(DAYS))
    world = null.copy()
    after = np.asarray(DAYS >= pd.Timestamp(START))
    world[after] += rng.normal(0, 3, after.sum())
    world[_window()] -= 30
    row = _summary(null, world)
    assert row["pre_onset_rms_diff"] == 0.0 and row["pre_onset_max_abs_diff"] == 0.0
    assert row["first_diff"] == START
    assert row["noise_floor"] > 0 and row["moved"] and row["onset"] == START


def test_identical_twins_and_noiseless_changes():
    null = np.full(len(DAYS), 50.0)
    same = _summary(null, null)
    assert not same["moved"] and same["noise_floor"] == 0 and same["onset"] is None
    assert same["rel_change"] == 0.0 and same["z_window"] is None
    world = null.copy()
    world[_window()] = 40.0
    exact = _summary(null, world)
    assert exact["moved"] and exact["rel_change"] == pytest.approx(-0.2)
    # The only two steps are the anomaly switching on and off: no noise, so
    # there is nothing to divide by and every differing day counts.
    assert exact["noise_floor"] == 0 and exact["z_window"] is None
    assert exact["days_over_floor"] == _window().sum()
    assert exact["onset"] == START and exact["offset"] == END
    last = null.copy()
    last[-1] = 49.0
    effects = twin_effects(_series(null), _series(last), {"m": 0.0})
    tail = summarize_twin(effects, START, None).iloc[0]
    assert tail["noise_floor"] == 0 and tail["z_window"] is None and tail["moved"]


def test_a_random_walk_is_not_mistaken_for_an_effect():
    """A stock's twin difference wanders; without the stock rule a wander
    twenty sigmas of step noise wide would read as a huge window z."""
    rng = np.random.default_rng(3)
    null = 1000 + np.cumsum(rng.normal(0, 1, len(DAYS)))
    wander = np.cumsum(rng.normal(0, 1, len(DAYS)))
    row = _summary(null, null + wander)
    assert row["stock_like"]
    assert abs(row["z_window"]) < NOISE_K and not row["moved"]


def test_a_weekly_feed_is_measured_on_its_active_days():
    weekly = np.where(DAYS.dayofweek == 0, 700.0, 0.0)
    rng = np.random.default_rng(4)
    mondays = weekly > 0
    world = weekly.copy()
    world[mondays] += rng.normal(0, 10, mondays.sum())
    world[mondays & _window()] *= 0.5
    row = _summary(weekly, world)
    assert row["moved"] and row["rel_change"] == pytest.approx(-0.5, abs=0.05)
    assert row["days_over_floor"] == (mondays & _window()).sum()


def test_a_slice_missing_from_one_world_is_filled():
    null = _series(np.ones(len(DAYS)), dimension="channel", value="a")
    world = pd.concat(
        [null, _series(np.full(len(DAYS), 2.0), dimension="channel", value="b")], ignore_index=True
    )
    effects = twin_effects(null, world, {"m": 0.0})
    b = effects[effects["value"] == "b"]
    assert (b["null_value"] == 0.0).all() and (b["diff"] == 2.0).all()
    unfilled = twin_effects(null, world, {"m": None})
    assert unfilled[unfilled["value"] == "b"]["diff"].isna().all()


def test_ground_truth_fields_sort_declared_metrics():
    rng = np.random.default_rng(5)

    def noise() -> np.ndarray:
        return rng.normal(0, 1, len(DAYS))

    base = 100 + noise()
    hit = base + noise()
    hit[_window()] -= 40
    null = pd.concat(
        [
            _series(base, "total_moves"),
            _series(base, "slice_moves"),
            _series(base, "slice_moves", "channel", "paid"),
            _series(base, "quiet"),
        ]
    )
    world = pd.concat(
        [
            _series(hit, "total_moves"),
            _series(base + noise(), "slice_moves"),
            _series(hit, "slice_moves", "channel", "paid"),
            _series(base + noise(), "quiet"),
        ]
    )
    summary = summarize_twin(twin_effects(null, world, {}), START, END)
    fields = ground_truth_fields(
        summary,
        ["total_moves", "slice_moves", "quiet", "not_computed"],
        "slice_moves",
        {"channel": "paid"},
    )
    assert fields["declared_affected_metrics_confirmed"] == {
        "moved": ["total_moves"],
        "moved_in_slice_only": ["slice_moves"],
        "not_detected": ["quiet"],
    }
    assert fields["root_metric_confirmed"] is True
    assert fields["root_effect"]["rel_change"] == pytest.approx(-0.4, abs=0.03)
    assert set(fields["measured_effects"]) == {"total_moves", "slice_moves", "quiet"}
    json.dumps(fields)  # JSON-ready: no NaN, numpy scalars or dates left

    silent = ground_truth_fields(summary, [], "quiet", None)
    assert silent["root_metric_confirmed"] is False


def test_cross_seed_summary():
    rows = []
    for seed, rel in enumerate([-0.30, -0.34, -0.26]):
        rows.append(
            {
                "regime": "baseline",
                "seed": seed,
                "anomaly": "a",
                "start": START,
                "metric": "m",
                "dimension": None,
                "value": None,
                "null_mean": 100.0,
                "mean_diff": 100 * rel,
                "rel_change": rel,
                "pre_onset_rms_diff": 1.0,
                "noise_floor": 4.0,
                "first_diff": START,
                "onset": START + dt.timedelta(days=seed),
                "offset": END,
                "days_over_floor": 10,
                "z_window": -9.0,
                "moved": seed != 2,
            }
        )
    out = summarize_across_seeds(pd.DataFrame(rows)).iloc[0]
    assert out["n_seeds"] == 3
    assert out["rel_change_mean"] == pytest.approx(-0.30)
    assert out["rel_change_sd"] == pytest.approx(0.04)
    assert out["rel_change_sem"] == pytest.approx(0.04 / np.sqrt(3))
    assert out["t_across_seeds"] == pytest.approx(-0.30 / (0.04 / np.sqrt(3)))
    assert out["moved_share"] == pytest.approx(2 / 3)
    assert out["onset_median_lag_days"] == 1


# --------------------------------------------------------------------------- #
# Smoke world sets: declared effects, and what the twins share
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def smoke_sets(tmp_path_factory) -> dict[str, tuple[Path, object]]:
    sets = {}
    for name in SMOKES:
        out = tmp_path_factory.mktemp("effects") / name
        sets[name] = (
            out,
            build_world_set(
                REPO_ROOT / "configs" / f"{name}.yaml",
                out,
                seeds=SEEDS,
                singles="all",
                fmt="parquet",
                jobs=2,
            ),
        )
    return sets


def _truth(out: Path, world) -> dict:
    return json.loads((out / world.ground_truth).read_text())[0]


@pytest.mark.parametrize("name", SMOKES)
def test_root_effect_has_the_declared_sign_and_size(name, smoke_sets):
    """Every smoke rate anomaly, every seed: the root metric at the root slice
    moves in the declared direction by at least the size the ground-truth
    honesty suites ask for (within 0.25 of the declared multiplier)."""
    out, manifest = smoke_sets[name]
    checked = 0
    for world, _ in manifest.twins():
        truth = _truth(out, world)
        if truth["kind"] != "rate":
            assert truth["root_metric"] is None and "root_effect" not in truth
            assert truth["measured_effects"], "dq anomalies are measured too"
            continue
        root = truth["root_effect"]
        assert truth["root_metric_confirmed"] is True, (world.id, root)
        declared = truth["magnitude"] - 1.0
        assert np.sign(root["rel_change"]) == np.sign(declared), (world.id, root)
        assert abs(root["rel_change"] - declared) < 0.25, (world.id, root["rel_change"], declared)
        assert abs(root["rel_change"]) > 0.5 * abs(declared)
        assert abs(root["z_window"]) >= NOISE_K
        # A sparse count (a few leads a day) can pass the window test without
        # any single day clearing the per-day floor: then there is no onset.
        assert root["onset"] is None or root["onset"] >= truth["start"]
        checked += 1
    assert checked == len(SEEDS)


@pytest.mark.parametrize("name", SMOKES)
def test_null_world_has_no_anomaly_sized_excursion(name, smoke_sets):
    """In the null world the root metric's level inside each scripted window
    is within half the declared effect of its level outside; in the single
    world it is not. (Both read off the twin's effects table.)"""
    out, manifest = smoke_sets[name]
    for world, null in manifest.twins():
        truth = _truth(out, world)
        if truth["kind"] != "rate":
            continue
        assert json.loads((out / null.ground_truth).read_text()) == []
        effects = pd.read_parquet(out / world.effects)
        rows = effects[effects["metric"] == truth["root_metric"]]
        rows = rows[
            (rows["dimension"] == truth["root_dimension"]) & (rows["value"] == truth["root_value"])
        ]
        assert len(rows) == manifest_days(out, world)
        day = pd.to_datetime(rows["date"])
        inside = (day >= truth["start"]) & (day <= truth["end"])
        half = 0.5 * abs(truth["magnitude"] - 1.0)

        in_null, in_world = (
            _excursion(rows, inside, "null_value"),
            _excursion(rows, inside, "world_value"),
        )
        assert abs(in_null) < half, (world.id, in_null)
        assert abs(in_world) > half, (world.id, in_world)


def _excursion(rows: pd.DataFrame, inside: pd.Series, column: str) -> float:
    """Mean inside the window relative to the mean outside it, minus one."""
    return rows.loc[inside, column].mean() / rows.loc[~inside, column].mean() - 1.0


def manifest_days(out: Path, world) -> int:
    return int(json.loads((out / world.run_manifest).read_text())["n_days"])


# What a single world shares with its null twin, per vertical's smoke rate
# anomaly. Measured, not designed: it follows from the order in which each
# entity builder consumes its random stream (see ``fake_companies.effects``).
#
# ``identical``  tables equal row for row over the whole timeline (the
#                anomaly cannot reach them and their stream is untouched);
# ``pre_onset``  tables whose rows dated before the anomaly's start are
#                equal, ``_loaded_at`` included;
# every other table already differs before the anomaly starts.
TWIN_COUPLING = {
    # conversion_rate.paid_search x mobile: session conversion draws one
    # uniform per session in time order, so sessions (and the first orders
    # they decide) match until the window. Repeat orders are drawn per
    # customer, baskets per order: one changed conversion re-deals them all.
    "smoke_retail_90d": {
        "identical": {"ad_platform.ad_spend", "shop_db.products"},
        "pre_onset": {"web.sessions"},
        "zero_noise_metrics": {
            "marketing_spend",
            "sessions",
            "conversion_rate",
            "converting_sessions",
            "new_customers",
            "session_orders",
        },
        "noisy_metrics": {"orders", "units", "gross_revenue", "net_revenue", "refunds"},
    },
    # spend.paid_search: ad spend and sessions are drawn channel by channel
    # from one stream, so every channel after paid_search is re-dealt for the
    # whole timeline and everything downstream follows. Even the plans
    # reference table differs: `_loaded_at` lags come from one stream consumed
    # table by table, so a changed row count upstream re-deals every later
    # table's lags.
    "smoke_90d": {
        "identical": set(),
        "pre_onset": set(),
        "zero_noise_metrics": {"marketing_spend"},
        "noisy_metrics": {"sessions", "signups", "trials_started", "mrr", "dau"},
    },
    # leads.outbound: lead counts are drawn source by source and every deal
    # attribute after them comes from the same stream.
    "smoke_b2b_90d": {
        "identical": set(),
        "pre_onset": set(),
        "zero_noise_metrics": set(),
        "noisy_metrics": {"leads", "qualified_deals", "open_deals", "bookings"},
    },
    # consumer_demand.teas: scan sales are one Poisson array drawn week by
    # week, shipments follow them, marketing is decoupled.
    "smoke_cpg_90d": {
        "identical": {
            "ad_platform.ad_spend",
            "erp.accounts",
            "erp.products",
            "promo.trade_promotions",
        },
        "pre_onset": {"erp.shipments", "pos.scan_sales"},
        "zero_noise_metrics": {
            "marketing_spend",
            "pos_units",
            "pos_dollars",
            "shipped_cases",
            "wholesale_net_revenue",
        },
        "noisy_metrics": set(),
    },
}


# Metrics dated by the start of a cohort, not by the day something happened.
# An anomaly reaches back into them: product events dropped in March belong
# to trials that started in the days before, so `trial_active_days` (bucketed
# by trial start) differs before the anomaly's window. That difference is
# causal, not noise, and it is the one case where "before the anomaly" is not
# clean.
COHORT_DATED = {"smoke_90d": {"trial_active_days", "trial_days_active"}}


def _pre_onset(result, spec, start: pd.Timestamp) -> pd.DataFrame:
    frame = result.frames[spec.fqn]
    ref = event_reference(spec, frame, result.calendar)
    return frame[(ref < start).to_numpy()].reset_index(drop=True)


@pytest.mark.parametrize("name", SMOKES)
def test_twin_tables_before_the_anomaly(name):
    """Which raw tables a single world shares with its null twin.

    The spec expected every table to match before the anomaly's start. That
    holds for dq anomalies and for builders that draw in time order; it does
    not hold for the others, and this pins which is which. If a table listed
    as diverging starts to match, the coupling got tighter: move it up.
    """
    cfg = load_config(REPO_ROOT / "configs" / f"{name}.yaml")
    specs = get_vertical(cfg.company.vertical).tables()
    null = generate(cfg, anomalies="none")
    for anomaly in cfg.anomalies.scripted:
        world = generate(cfg, anomalies=f"only:{anomaly.name}")
        start = pd.Timestamp(anomaly.window.start)
        identical = {s.fqn for s in specs if null.frames[s.fqn].equals(world.frames[s.fqn])}
        pre = {
            s.fqn
            for s in specs
            if not s.reference_data
            and _pre_onset(null, s, start).equals(_pre_onset(world, s, start))
        }
        if anomaly.kind == "dq":
            # The observation layer touches its target table and nothing else,
            # and only rows inside the window.
            assert identical == {s.fqn for s in specs} - {anomaly.target}
            assert anomaly.target in pre
            continue
        expected = TWIN_COUPLING[name]
        assert identical == expected["identical"], (name, anomaly.name)
        assert pre - identical == expected["pre_onset"], (name, anomaly.name)


@pytest.mark.parametrize("name", SMOKES)
def test_twin_metric_noise_before_the_anomaly(name, smoke_sets):
    """The twin difference before the anomaly is exactly zero for the metrics
    built from aligned streams and for every metric of a dq twin; for the rest
    it is realization noise, and the ground truth reports its size."""
    out, manifest = smoke_sets[name]
    expected = TWIN_COUPLING[name]
    for world, _ in manifest.twins():
        truth = _truth(out, world)
        measured = truth["measured_effects"]
        zero = {m for m, e in measured.items() if e["pre_onset_rms_diff"] == 0}
        if truth["kind"] == "dq":
            assert zero == set(measured) - COHORT_DATED.get(name, set()), world.id
            continue
        assert expected["zero_noise_metrics"] <= zero, (
            world.id,
            expected["zero_noise_metrics"] - zero,
        )
        assert not (expected["noisy_metrics"] & zero), (world.id, expected["noisy_metrics"] & zero)
        for metric in expected["noisy_metrics"]:
            assert measured[metric]["noise_floor"] > 0


def test_float_summation_order_is_not_a_difference():
    null = np.full(len(DAYS), 1234.5)
    world = null * (1 + 1e-13)
    effects = twin_effects(_series(null), _series(world), {"m": 0.0})
    assert (effects["diff"] == 0).all()
    assert not summarize_twin(effects, START, END).iloc[0]["moved"]


def test_reshuffled_timing_does_not_hide_a_sparse_effect():
    """A return-rate anomaly re-deals the request delay of every later return:
    the twins differ on most later days, but only by returns landing on other
    days. A white-noise standard error would bury the effect; placebo windows
    see that the reshuffle cancels inside a window."""
    days = pd.date_range("2024-01-01", periods=600, freq="D")
    rng = np.random.default_rng(6)
    null = rng.poisson(3.0, len(days)).astype(float)
    start, end = dt.date(2024, 4, 1), dt.date(2024, 5, 5)
    window = np.asarray((days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end)))
    after = np.flatnonzero(days >= pd.Timestamp(start))
    world = null.copy()
    for i in after[:-1]:  # every later return may slip a day either way
        moved = rng.binomial(int(world[i]), 0.3)
        world[i] -= moved
        world[i + 1] += moved
    world[window] += rng.poisson(1.5, window.sum())
    frame = pd.DataFrame({"date": days, "metric": "m", "dimension": None, "value": None})
    effects = twin_effects(frame.assign(v=null), frame.assign(v=world), {"m": 0.0})
    row = summarize_twin(effects, start, end).iloc[0]
    assert row["pre_onset_rms_diff"] == 0 and row["first_diff"] == start
    assert row["rel_change"] == pytest.approx(0.5, abs=0.15)
    assert row["moved"] and row["z_window"] > NOISE_K
