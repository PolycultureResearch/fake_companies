"""B2B ground-truth honesty: each scripted anomaly is recoverable from raw rows."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.slow
def test_outbound_lead_drop_recoverable(b2b_cfg, b2b_result):
    spec = next(a for a in b2b_cfg.anomalies.scripted if a.name == "outbound_team_turnover")
    deals = b2b_result.frames["crm.deals"].drop_duplicates("deal_id")
    sub = deals[deals["source"] == "outbound"]
    day = pd.to_datetime(sub["created_at"]).dt.normalize()
    win0, win1 = pd.Timestamp(spec.window.start), pd.Timestamp(spec.window.end)

    daily = day.value_counts().sort_index()
    # Compare weekday-matched daily counts (B2B volume is strongly weekday-shaped).
    dows = pd.Series(daily.index.dayofweek, index=daily.index)
    in_win = (daily.index >= win0) & (daily.index <= win1)
    clean = daily[~in_win]
    clean_dow_mean = clean.groupby(dows[~in_win]).mean()

    expected = dows[in_win].map(clean_dow_mean)
    ratio = daily[in_win].sum() / expected.sum()
    assert ratio < spec.magnitude + 0.2, (ratio, spec.magnitude)

    # Other sources stay clean.
    other = deals[deals["source"] != "outbound"]
    oday = pd.to_datetime(other["created_at"]).dt.normalize()
    odaily = oday.value_counts().sort_index()
    o_in = (odaily.index >= win0) & (odaily.index <= win1)
    odows = pd.Series(odaily.index.dayofweek, index=odaily.index)
    o_clean_mean = odaily[~o_in].groupby(odows[~o_in]).mean()
    o_ratio = odaily[o_in].sum() / odows[o_in].map(o_clean_mean).sum()
    assert 0.8 < o_ratio < 1.2, o_ratio


@pytest.mark.slow
def test_deal_dupes_recoverable(b2b_cfg, b2b_result):
    spec = next(a for a in b2b_cfg.anomalies.scripted if a.name == "deals_pipeline_dupes")
    deals = b2b_result.frames["crm.deals"]
    day = pd.to_datetime(deals["created_at"]).dt.normalize()
    win0, win1 = pd.Timestamp(spec.window.start), pd.Timestamp(spec.window.end)
    in_win = (day >= win0) & (day <= win1)

    assert deals.loc[in_win, "deal_id"].duplicated().sum() > 0
    assert not deals.loc[~in_win, "deal_id"].duplicated().any()
