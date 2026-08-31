"""CPG ground-truth honesty: each scripted anomaly is recoverable from raw rows."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.slow
def test_teas_demand_drop_recoverable(cpg_cfg, cpg_result):
    spec = next(a for a in cpg_cfg.anomalies.scripted if a.name == "teas_demand_scare")
    pos = cpg_result.frames["pos.scan_sales"].drop_duplicates("scan_id")
    teas = pos[pos["category"] == "teas"]
    weekly = teas.groupby("week_start")["units"].sum()
    weeks = pd.to_datetime(weekly.index)

    win0 = pd.Timestamp(spec.window.start)
    win1 = pd.Timestamp(spec.window.end)
    # A week is affected when it overlaps the anomaly window.
    in_win = (weeks + pd.Timedelta(days=6) >= win0) & (weeks <= win1)
    assert in_win.sum() >= 2

    clean = weekly[~in_win]
    med = clean.median()
    mad = (clean - med).abs().median()
    robust_sigma = max(1.4826 * mad, 1.0)
    z = (weekly[in_win] - med) / robust_sigma
    assert (z < -3).all(), (weekly[in_win], med, robust_sigma)

    # Other categories stay clean in the same weeks.
    other = pos[pos["category"] != "teas"].groupby("week_start")["units"].sum()
    o_clean = other[~in_win]
    o_z = (other[in_win] - o_clean.median()) / max(
        1.4826 * (o_clean - o_clean.median()).abs().median(), 1.0
    )
    assert (o_z.abs() < 3).all(), o_z


@pytest.mark.slow
def test_pos_outage_recoverable(cpg_cfg, cpg_result):
    spec = next(a for a in cpg_cfg.anomalies.scripted if a.name == "pos_feed_outage")
    pos = cpg_result.frames["pos.scan_sales"]
    weekly_rows = pos.groupby("week_start").size()
    weeks = pd.to_datetime(weekly_rows.index)
    win0, win1 = pd.Timestamp(spec.window.start), pd.Timestamp(spec.window.end)
    in_win = (weeks >= win0) & (weeks <= win1)
    assert in_win.any()
    clean_median = weekly_rows[~in_win].median()
    # volume_dropout kept ~magnitude of rows.
    ratio = weekly_rows[in_win].mean() / clean_median
    assert ratio < spec.magnitude + 0.15, (ratio, spec.magnitude)

    # The demand cascade check: shipments the following week were placed
    # against true (pre-outage) demand, so wholesale stays healthy — the
    # outage is a data-quality event, not a business event.
    ship = cpg_result.frames["erp.shipments"].drop_duplicates("shipment_id")
    ship_week = pd.to_datetime(ship["shipped_at"]).dt.to_period("W-SUN").dt.start_time
    weekly_cases = ship.groupby(ship_week)["cases"].sum()
    after = weekly_cases[
        (weekly_cases.index > win1) & (weekly_cases.index <= win1 + pd.Timedelta(days=14))
    ]
    clean_cases = weekly_cases[(weekly_cases.index < win0)]
    assert after.mean() > 0.6 * clean_cases.median(), (after.mean(), clean_cases.median())


@pytest.mark.slow
def test_ground_truth_covers_all_scripted(cpg_cfg, cpg_result):
    ids = {g.id for g in cpg_result.ground_truth}
    assert {a.name for a in cpg_cfg.anomalies.scripted} <= ids
