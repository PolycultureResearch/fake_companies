"""Retail ground-truth honesty: each scripted anomaly is recoverable from raw rows.

Same convention as the SaaS honesty suite: recompute the affected aggregate
from the generated frames and require a clear shift (~3+ robust sigmas or an
explicit magnitude check) inside the window vs clean days.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.normalize()


@pytest.mark.slow
def test_mobile_conversion_drop_recoverable(retail_cfg, retail_result):
    spec = next(a for a in retail_cfg.anomalies.scripted if a.name == "mobile_checkout_regression")
    sessions = retail_result.frames["web.sessions"]
    win0, win1 = pd.Timestamp(spec.window.start), pd.Timestamp(spec.window.end)

    sub = sessions[(sessions["channel"] == "paid_search") & (sessions["device"] == "mobile")]
    day = _day(sub["started_at"])
    in_win = (day >= win0) & (day <= win1)
    conv_in = sub.loc[in_win, "user_id"].notna().mean()
    conv_out = sub.loc[~in_win, "user_id"].notna().mean()

    n_in = int(in_win.sum())
    se = np.sqrt(conv_out * (1 - conv_out) / max(n_in, 1))
    assert conv_in < conv_out - 3 * se, (conv_in, conv_out)
    # And the magnitude is in the right ballpark.
    assert conv_in / conv_out < spec.magnitude + 0.25

    # Desktop stays clean (segmented anomaly leaves other segments alone).
    desk = sessions[(sessions["channel"] == "paid_search") & (sessions["device"] == "desktop")]
    dday = _day(desk["started_at"])
    d_in = (dday >= win0) & (dday <= win1)
    conv_d_in = desk.loc[d_in, "user_id"].notna().mean()
    conv_d_out = desk.loc[~d_in, "user_id"].notna().mean()
    n_d = int(d_in.sum())
    se_d = np.sqrt(conv_d_out * (1 - conv_d_out) / max(n_d, 1))
    assert conv_d_in > conv_d_out - 3 * se_d


@pytest.mark.slow
def test_order_dupes_recoverable(retail_cfg, retail_result):
    spec = next(a for a in retail_cfg.anomalies.scripted if a.name == "orders_pipeline_dupes")
    orders = retail_result.frames["shop_db.orders"]
    day = _day(orders["placed_at"])
    win0, win1 = pd.Timestamp(spec.window.start), pd.Timestamp(spec.window.end)
    in_win = (day >= win0) & (day <= win1)

    # PK uniqueness breaks inside the window.
    dupes = orders.loc[in_win, "order_id"].duplicated().sum()
    assert dupes > 0
    assert not orders.loc[~in_win, "order_id"].duplicated().any()

    # Daily volume is elevated ~(1 + magnitude) in the window.
    daily = day.value_counts().sort_index()
    win_days = daily[(daily.index >= win0) & (daily.index <= win1)]
    clean = daily[(daily.index < win0) | (daily.index > win1)]
    med, mad = clean.median(), (clean - clean.median()).abs().median()
    robust_sigma = 1.4826 * mad
    assert ((win_days - med) / robust_sigma > 3).all(), (win_days, med, robust_sigma)
