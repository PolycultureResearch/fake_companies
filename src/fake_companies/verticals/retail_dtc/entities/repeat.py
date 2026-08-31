"""Repeat purchases: a monthly-hazard engine over existing customers.

Each calendar month after a customer's first-order month, they place a repeat
order with probability ``repeat_rate`` (the latent driver's monthly mean) times
a per-customer lognormal frailty — the retail analog of the SaaS lifecycle's
monthly hazards, one hazard instead of five. Realized orders land on a day
within the month weighted by the volume seasonality, with no session link
(channel drawn from the configured repeat mix; email/direct heavy).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ....latent.shape import seasonality_volume
from ....shared._util import SESSION_HOUR_WEIGHTS, intraday_seconds, sample_labels
from ..config import RetailDTCScenarioConfig as ScenarioConfig


def build_repeat_orders(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    first_orders: pd.DataFrame,
) -> pd.DataFrame:
    """Order skeleton rows (same columns as first orders) for repeat purchases."""
    gen = rng.stream("retail_repeat")
    n_cust = len(first_orders)
    if n_cust == 0:
        return first_orders.iloc[0:0].copy()

    # Month grid over the timeline.
    month_of_day = (cal.dates.year * 12 + cal.dates.month).to_numpy()
    month_of_day = month_of_day - month_of_day[0]  # 0-based month index per day
    n_months = int(month_of_day[-1]) + 1

    # Per-customer lognormal frailty on the repeat hazard, mean ~1.
    sigma = cfg.repeat.frailty_sigma
    frailty = np.exp(gen.normal(0.0, sigma, size=n_cust) - 0.5 * sigma**2)

    # Monthly mean of the repeat_rate driver (anomalies on it land here).
    rate = panel.get("repeat_rate")
    month_rate = np.bincount(month_of_day, weights=rate, minlength=n_months) / np.bincount(
        month_of_day, minlength=n_months
    )

    # Eligibility: months strictly after the first-order month.
    first_day = first_orders["day_index"].to_numpy()
    first_month = month_of_day[first_day]

    p = frailty[:, None] * month_rate[None, :]  # (n_cust, n_months)
    p = np.clip(p, 0.0, 0.95)
    eligible = np.arange(n_months)[None, :] > first_month[:, None]
    hit = (gen.random((n_cust, n_months)) < p) & eligible
    cust_idx, month_idx = np.nonzero(hit)
    n_rep = len(cust_idx)
    if n_rep == 0:
        return first_orders.iloc[0:0].copy()

    # Day within the month, weighted by volume seasonality.
    season = seasonality_volume(cfg, cal)
    order = np.argsort(month_of_day, kind="stable")  # days already sorted, but be explicit
    day_sorted = order
    month_sorted = month_of_day[day_sorted]
    starts = np.searchsorted(month_sorted, np.arange(n_months))
    counts = np.bincount(month_of_day, minlength=n_months)

    # Per-month cumulative seasonality for day sampling.
    day_choice = np.empty(n_rep, dtype=np.int64)
    u = gen.random(n_rep)
    for m in range(n_months):  # <= ~26 iterations, not per-entity
        sel = month_idx == m
        if not sel.any():
            continue
        days_m = day_sorted[starts[m] : starts[m] + counts[m]]
        w = season[days_m]
        cw = np.cumsum(w) / w.sum()
        day_choice[sel] = days_m[np.searchsorted(cw, u[sel], side="right").clip(0, len(days_m) - 1)]

    secs = intraday_seconds(gen, day_choice, SESSION_HOUR_WEIGHTS)
    placed_at = np.datetime64(cal.start, "s") + (
        day_choice.astype("int64") * 86400 + secs.astype("int64")
    ).astype("timedelta64[s]")

    channels = list(cfg.repeat.channel_mix)
    channel_p = list(cfg.repeat.channel_mix.values())

    customers = first_orders.iloc[cust_idx]
    return pd.DataFrame(
        {
            "customer_id": customers["customer_id"].to_numpy(),
            "session_id": pd.array([pd.NA] * n_rep, dtype="Int64"),
            "channel": sample_labels(gen, channels, channel_p, n_rep),
            "country": customers["country"].to_numpy(),
            "device": customers["device"].to_numpy(),
            "is_first_order": False,
            "placed_at": placed_at,
            "day_index": day_choice,
        }
    )
