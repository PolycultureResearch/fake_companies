"""``product.events`` — per-user daily activity, drawn from engagement drivers.

Each user's timeline is split into plan segments (a free segment before any paid
spell, then one segment per active subscription spell on its plan). Every
user-day inside a segment is drawn in two steps, exactly as ``trial_activity``
draws the trial window:

1. **Is the user active?** ``p_active[u, d] = dau_over_active[plan][d] *
   weekend_uplift[d] * member_engagement[d] (paid only) * frailty[u]``, capped at
   ``MAX_P_ACTIVE``. So ``dau_over_active`` *is* the realized daily active share,
   which is what the config says it is.
2. **How many events, given active?** ``1 + Poisson(events_per_active_day - 1)``
   — the conditional mean on an active day, zero otherwise.

Both draws go through the shared helpers in ``_util`` so the two activity
generators hold one reading of the knob. (Until 2026-08 this module instead
multiplied ``dau_over_active`` into an NHPP intensity, ``lam = dau x
events_per_active_day``: at lam ~1.7-5.3 events/day the realized active share
came out 0.77-0.93 instead of the configured 0.14/0.24, which pinned the
downstream ``member_activity_rate`` metric against its ceiling and left the
weekly engagement signal squashed into the flat part of ``1 - exp(-lam)``.)

The (segment x day) expansion is a ragged flat array processed in bounded chunks
— vectorized throughout, no per-user or per-day Python loop.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ._util import (
    USAGE_HOUR_WEIGHTS,
    active_day_mask,
    events_on_active_days,
    intraday_seconds,
    sample_labels,
    timestamps_from_days,
)
from .plans import PlanIndex

# Cap on the user-days materialized at once. A multi-year scenario has tens of
# millions of segment-days; chunking keeps peak memory flat without giving up
# vectorization (each chunk is still one numpy pass).
_CHUNK_USER_DAYS = 4_000_000


def build_usage(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    plan_index: PlanIndex,
    frames: dict[str, pd.DataFrame],
    frailty: np.ndarray | None = None,
    trial_events: pd.DataFrame | None = None,
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
    # The shared engagement propensity when the orchestrator provides it (so
    # the same latent drives usage, conversion and churn); a local draw
    # otherwise, preserving the standalone behavior.
    if frailty is None:
        frailty = _frailty(gen, n_users, cfg.engagement.frailty_sigma)

    # Trial windows are covered by trial_activity.py when its events are
    # handed in — the free segment then starts at trial end, so no user-day is
    # generated twice.
    trial_end_day = created_day.copy()
    if trial_events is not None:
        started_trial = np.zeros(n_users, dtype=bool)
        started_trial[uid0] = users["started_trial"].to_numpy(dtype=bool)
        trial_end_day = np.where(
            started_trial,
            np.minimum(created_day + cfg.lifecycle.trial_days, n_days),
            created_day,
        )

    # --- assemble plan segments -------------------------------------------- #
    # Plans that generate events = the engagement config's plan keys; the free /
    # zero-price tier is the pre-conversion baseline. Both derived from config.
    plan_names = list(cfg.engagement.dau_over_active)
    free_name = plan_index.free_name
    paid_names = [p for p in plan_names if p != free_name]

    subs = frames.get("app_db.subscriptions")
    seg_user: dict[str, list] = {p: [] for p in plan_names}
    seg_start: dict[str, list] = {p: [] for p in plan_names}
    seg_end: dict[str, list] = {p: [] for p in plan_names}

    first_paid = np.full(n_users, n_days, dtype=np.int64)
    if subs is not None and len(subs):
        paid = subs[subs["started_at"].notna() & subs["plan_id"].notna()]
        if len(paid):
            p_uid = paid["user_id"].to_numpy(dtype=np.int64)
            p_start = _to_day(paid["started_at"], cal, n_days)
            p_end = np.where(
                paid["canceled_at"].notna().to_numpy(),
                _to_day(paid["canceled_at"], cal, n_days),
                n_days,
            )
            p_plan = np.array(
                [plan_index.row(int(p)).name for p in paid["plan_id"].to_numpy()], dtype=object
            )
            # earliest paid start per user -> end of the free pre-conversion segment
            np.minimum.at(first_paid, p_uid - 1, p_start)
            for plan in paid_names:
                m = p_plan == plan
                seg_user[plan].append(p_uid[m] - 1)
                seg_start[plan].append(p_start[m])
                seg_end[plan].append(p_end[m])

    # free/baseline segment for every user: [created_day, first_paid_start) —
    # starting at trial end where trial_activity already generated the window.
    if free_name is not None:
        free_end = np.minimum(first_paid, n_days)
        seg_user[free_name].append(uid0)
        seg_start[free_name].append(trial_end_day[uid0])
        seg_end[free_name].append(free_end[uid0])

    # --- generate events per plan group ------------------------------------ #
    parts: list[pd.DataFrame] = []
    for plan in plan_names:
        if not seg_user[plan]:
            continue
        su = np.concatenate(seg_user[plan]).astype(np.int64)
        ss = np.concatenate(seg_start[plan]).astype(np.int64)
        se = np.concatenate(seg_end[plan]).astype(np.int64)
        valid = se > ss
        su, ss, se = su[valid], ss[valid], se[valid]
        if len(su) == 0:
            continue
        part = _events_for_plan(
            cfg, cal, gen, panel, plan, su, ss, se, frailty, country, device, plan != free_name
        )
        if part is not None:
            parts.append(part)

    if trial_events is not None and len(trial_events):
        parts.append(trial_events)
    if not parts:
        frames["product.events"] = _empty_events()
        return

    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values("occurred_at", kind="stable").reset_index(drop=True)
    df.insert(0, "event_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT
    frames["product.events"] = df


def _events_for_plan(cfg, cal, gen, panel, plan, su, ss, se, frailty, country, device, paid=False):
    """One plan's segments -> product events, gated on a Bernoulli active day."""
    eng = cfg.engagement
    weekend = np.where(np.isin(cal.dow, [5, 6]), eng.weekend_uplift, 1.0)
    day_rate = panel.get(f"dau_over_active.{plan}") * weekend
    if paid:
        # Paid-tier activity rides the shared member_engagement driver — the same
        # one that (inversely) scales churn hazard in lifecycle.py, which is what
        # makes the member-activity -> churn edge learnable weekly. It moves the
        # active *share* now, so the weekly metric tracks it linearly instead of
        # through the saturated tail of a Poisson intensity.
        day_rate = day_rate * panel.get("member_engagement")
    epd = panel.get(f"events_per_active_day.{plan}")

    feats = list(eng.feature_mix)
    fp = list(eng.feature_mix.values())

    seg_len = (se - ss).astype(np.int64)
    offset = np.concatenate([[0], np.cumsum(seg_len)])  # flat start index per segment
    n_seg = len(su)

    parts: list[pd.DataFrame] = []
    lo = 0
    while lo < n_seg:
        hi = int(np.searchsorted(offset, offset[lo] + _CHUNK_USER_DAYS, side="right")) - 1
        hi = min(max(hi, lo + 1), n_seg)

        sl = seg_len[lo:hi]
        n_cells = int(sl.sum())
        cell_seg = np.repeat(np.arange(lo, hi, dtype=np.int64), sl)
        # ragged arange: position within each segment, then its calendar day
        within = np.arange(n_cells, dtype=np.int64) - np.repeat(offset[lo:hi] - offset[lo], sl)
        cell_day = ss[cell_seg] + within

        active = active_day_mask(gen, day_rate[cell_day] * frailty[su[cell_seg]])
        counts = events_on_active_days(gen, active, epd[cell_day])
        lo = hi

        total = int(counts.sum())
        if total == 0:
            continue
        ev_cell = np.repeat(np.arange(n_cells, dtype=np.int64), counts)
        ev_day = cell_day[ev_cell]
        u = su[cell_seg[ev_cell]]
        sec = intraday_seconds(gen, ev_day, USAGE_HOUR_WEIGHTS)
        parts.append(
            pd.DataFrame(
                {
                    "user_id": (u + 1).astype(np.int64),
                    "event_name": sample_labels(gen, feats, fp, total),
                    "plan_at_event": plan,
                    "country": country[u],
                    "device": device[u],
                    "occurred_at": timestamps_from_days(cal.start, ev_day, sec),
                }
            )
        )

    if not parts:
        return None
    return parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)


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
