"""``product.events`` — NHPP usage events per user, drawn from engagement drivers.

Each user's timeline is split into plan segments (a free segment before any paid
spell, then one segment per active subscription spell on its plan). For each
plan, a daily intensity ``lam[d] = dau_over_active[d] * events_per_active_day[d]
* weekend_uplift`` is taken from the latent panel (so engagement anomalies and
the weekend uplift show up in the event stream). Per segment we draw a Poisson
count from the integral of ``lam`` over the segment, then place each event on a
day sampled ∝ ``lam`` (localizing dips) via an inverse-CDF search — all
vectorized, three plan groups, no per-user loops.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ._util import USAGE_HOUR_WEIGHTS, intraday_seconds, sample_labels, timestamps_from_days
from .plans import PlanIndex


def build_usage(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    plan_index: PlanIndex,
    frames: dict[str, pd.DataFrame],
) -> None:
    users = frames["app_db.users"]
    if len(users) == 0:
        frames["product.events"] = _empty_events()
        return
    gen = rng.stream("usage")
    n_days = cal.n_days

    # --- per-user attribute arrays, indexed by (user_id - 1) --------------- #
    n_users = int(users["user_id"].max())
    created_day = np.full(n_users, 0, dtype=np.int64)
    country = np.empty(n_users, dtype=object)
    device = np.empty(n_users, dtype=object)
    uid0 = users["user_id"].to_numpy(dtype=np.int64) - 1
    created_day[uid0] = users["day_index"].to_numpy(dtype=np.int64)
    country[uid0] = users["country"].to_numpy()
    device[uid0] = users["device_at_signup"].to_numpy()
    frailty = _frailty(gen, n_users, cfg.engagement.frailty_sigma)

    # --- assemble plan segments -------------------------------------------- #
    subs = frames.get("app_db.subscriptions")
    seg_user: dict[str, list] = {"free": [], "basic": [], "pro": []}
    seg_start: dict[str, list] = {"free": [], "basic": [], "pro": []}
    seg_end: dict[str, list] = {"free": [], "basic": [], "pro": []}

    first_paid = np.full(n_users, n_days, dtype=np.int64)
    if subs is not None and len(subs):
        paid = subs[subs["started_at"].notna()]
        p_uid = paid["user_id"].to_numpy(dtype=np.int64)
        p_start = _to_day(paid["started_at"], cal, n_days)
        p_end = np.where(
            paid["canceled_at"].notna().to_numpy(),
            _to_day(paid["canceled_at"], cal, n_days),
            n_days,
        )
        p_plan = np.array(
            [plan_index.row(int(p)).name for p in paid["plan_id"].fillna(0).to_numpy()],
            dtype=object,
        )
        # earliest paid start per user -> end of the free pre-conversion segment
        np.minimum.at(first_paid, p_uid - 1, p_start)
        for plan in ("basic", "pro"):
            m = p_plan == plan
            seg_user[plan].append(p_uid[m] - 1)
            seg_start[plan].append(p_start[m])
            seg_end[plan].append(p_end[m])

    # free segment for every user: [created_day, first_paid_start)
    free_end = np.minimum(first_paid, n_days)
    seg_user["free"].append(uid0)
    seg_start["free"].append(created_day[uid0])
    seg_end["free"].append(free_end[uid0])

    # --- generate events per plan group ------------------------------------ #
    parts: list[pd.DataFrame] = []
    for plan in ("free", "basic", "pro"):
        if not seg_user[plan]:
            continue
        su = np.concatenate(seg_user[plan]).astype(np.int64)
        ss = np.concatenate(seg_start[plan]).astype(np.int64)
        se = np.concatenate(seg_end[plan]).astype(np.int64)
        valid = se > ss
        su, ss, se = su[valid], ss[valid], se[valid]
        if len(su) == 0:
            continue
        part = _events_for_plan(cfg, cal, gen, panel, plan, su, ss, se, frailty, country, device)
        if part is not None:
            parts.append(part)

    if not parts:
        frames["product.events"] = _empty_events()
        return

    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values("occurred_at", kind="stable").reset_index(drop=True)
    df.insert(0, "event_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT
    frames["product.events"] = df


def _events_for_plan(cfg, cal, gen, panel, plan, su, ss, se, frailty, country, device):
    weekend = np.isin(cal.dow, [5, 6])
    lam = panel.get(f"dau_over_active.{plan}") * panel.get(f"events_per_active_day.{plan}")
    lam = lam * np.where(weekend, cfg.engagement.weekend_uplift, 1.0)

    # prefix integral P[d] = sum(lam[:d]); length n_days + 1
    prefix = np.concatenate([[0.0], np.cumsum(lam)])
    seg_sum = prefix[se] - prefix[ss]
    expected = frailty[su] * seg_sum
    n_ev = gen.poisson(np.maximum(expected, 0.0))
    total = int(n_ev.sum())
    if total == 0:
        return None

    ev_seg = np.repeat(np.arange(len(su)), n_ev)
    # inverse-CDF day placement weighted by lam within each segment
    lo = prefix[ss][ev_seg]
    span = seg_sum[ev_seg]
    target = lo + gen.random(total) * span
    day = np.searchsorted(prefix, target, side="right") - 1
    day = np.clip(day, ss[ev_seg], se[ev_seg] - 1)

    sec = intraday_seconds(gen, day, USAGE_HOUR_WEIGHTS)
    occurred = timestamps_from_days(cal.start, day, sec)
    u = su[ev_seg]
    feats = list(cfg.engagement.feature_mix)
    fp = list(cfg.engagement.feature_mix.values())

    return pd.DataFrame(
        {
            "user_id": (u + 1).astype(np.int64),
            "event_name": sample_labels(gen, feats, fp, total),
            "plan_at_event": plan,
            "country": country[u],
            "device": device[u],
            "occurred_at": occurred,
        }
    )


def _frailty(gen: np.random.Generator, n: int, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return np.ones(n)
    return gen.lognormal(-0.5 * sigma**2, sigma, size=n)


def _to_day(series: pd.Series, cal: Calendar, n_days: int) -> np.ndarray:
    days = (pd.to_datetime(series).dt.normalize() - pd.Timestamp(cal.start)).dt.days
    return np.clip(days.fillna(n_days).to_numpy(), 0, n_days).astype(np.int64)


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": pd.array([], dtype="int64"),
            "user_id": pd.array([], dtype="int64"),
            "event_name": pd.array([], dtype="object"),
            "plan_at_event": pd.array([], dtype="object"),
            "country": pd.array([], dtype="object"),
            "device": pd.array([], dtype="object"),
            "occurred_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
