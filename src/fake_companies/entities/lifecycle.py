"""Subscription lifecycle: ``app_db.subscriptions`` + ``subscription_events``.

A vectorized monthly-hazard state machine over trial-starting users. Each month
(~24 iterations, all numpy-vectorized over the user cohort) we process trial
conversions/expirations, then per-plan churn/upgrade/downgrade on active subs,
then resurrection of churned users. The machine only *emits events*; the spell
table is derived from the event stream afterward.

MRR-movement invariant: every event records ``from_plan_id`` / ``to_plan_id`` so
that ``sum(monthly_price(to) - monthly_price(from))`` over all events equals the
final active MRR (movements telescope). Trials carry no MRR (from=to=None).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ._util import timestamps_from_days
from .plans import PlanIndex

_DAYS_PER_MONTH = 30
_NAT = np.datetime64("NaT", "s")

# state codes
_TRIALING = 0
_ACTIVE = 1
_CHURNED = 2  # resurrectable
_EXPIRED = 3  # trial expired, resurrectable


def build_lifecycle(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    plan_index: PlanIndex,
    frames: dict[str, pd.DataFrame],
) -> None:
    users = frames["app_db.users"]
    gen = rng.stream("lifecycle")

    trial_mask = users["started_trial"].to_numpy(dtype=bool)
    trial_users = users.loc[trial_mask]
    if len(trial_users) == 0:
        frames["app_db.subscriptions"] = _empty_subscriptions()
        frames["app_db.subscription_events"] = _empty_events()
        return

    uid = trial_users["user_id"].to_numpy(dtype=np.int64)
    created_day = trial_users["day_index"].to_numpy(dtype=np.int64)
    U = len(uid)
    n_days = cal.n_days
    n_months = int(np.ceil(n_days / _DAYS_PER_MONTH))

    trial_days = cfg.lifecycle.trial_days
    trial_end_day = np.minimum(created_day + trial_days, n_days - 1)
    trial_end_month = trial_end_day // _DAYS_PER_MONTH

    # Paid-plan sampling helpers.
    paid_names = list(cfg.plans.plan_mix)
    paid_p = np.array([cfg.plans.plan_mix[n] for n in paid_names], dtype=float)
    paid_p /= paid_p.sum()
    annual_share = cfg.plans.annual_share

    # Per-user state.
    st = np.full(U, _TRIALING, dtype=np.int8)
    plan_id = np.zeros(U, dtype=np.int64)
    started_month = np.full(U, -1, dtype=np.int64)
    spell_no = np.zeros(U, dtype=np.int64)

    # trial_start events for everyone (spell 0), at created_at.
    ev = _EventBuffer()
    trial_start_sec = gen.integers(0, 86400, size=U)
    ev.add(uid, spell_no, "trial_start", 0, 0, created_day, trial_start_sec)

    churn_names = list(cfg.lifecycle.monthly_churn)

    for m in range(n_months):
        dm = min(m * _DAYS_PER_MONTH + 15, n_days - 1)

        # --- A) trial resolution for trials ending this month --------------- #
        ending = (st == _TRIALING) & (trial_end_month == m)
        if ending.any():
            idx = np.flatnonzero(ending)
            conv_p = panel.get("trial_convert")[trial_end_day[idx]]
            convert = gen.random(len(idx)) < conv_p
            conv_idx = idx[convert]
            exp_idx = idx[~convert]

            if len(conv_idx):
                pid = _sample_plan(gen, plan_index, paid_names, paid_p, annual_share, len(conv_idx))
                plan_id[conv_idx] = pid
                st[conv_idx] = _ACTIVE
                started_month[conv_idx] = m
                ev.add(
                    uid[conv_idx],
                    spell_no[conv_idx],
                    "trial_convert",
                    0,
                    pid,
                    trial_end_day[conv_idx],
                    gen.integers(0, 86400, size=len(conv_idx)),
                )
            if len(exp_idx):
                st[exp_idx] = _EXPIRED
                ev.add(
                    uid[exp_idx],
                    spell_no[exp_idx],
                    "trial_expire",
                    0,
                    0,
                    trial_end_day[exp_idx],
                    gen.integers(0, 86400, size=len(exp_idx)),
                )

        # --- B) monthly hazards on subs active before this month ------------ #
        active = (st == _ACTIVE) & (started_month < m)
        if active.any():
            _apply_hazards(
                gen,
                cfg,
                panel,
                plan_index,
                churn_names,
                active,
                st,
                plan_id,
                m,
                dm,
                n_days,
                uid,
                spell_no,
                ev,
            )

        # --- C) resurrection of churned/expired users ----------------------- #
        resurrectable = (st == _CHURNED) | (st == _EXPIRED)
        if resurrectable.any():
            idx = np.flatnonzero(resurrectable)
            res_p = panel.get("resurrect")[dm]
            resurrect = gen.random(len(idx)) < res_p
            res_idx = idx[resurrect]
            if len(res_idx):
                spell_no[res_idx] += 1
                pid = _sample_plan(gen, plan_index, paid_names, paid_p, annual_share, len(res_idx))
                plan_id[res_idx] = pid
                st[res_idx] = _ACTIVE
                started_month[res_idx] = m
                day = _month_day(gen, m, n_days, len(res_idx))
                ev.add(
                    uid[res_idx],
                    spell_no[res_idx],
                    "resurrect",
                    0,
                    pid,
                    day,
                    gen.integers(0, 86400, size=len(res_idx)),
                )

    events_df = ev.frame(cal)
    frames["app_db.subscription_events"] = _finalize_events(events_df, plan_index)
    frames["app_db.subscriptions"] = _derive_subscriptions(events_df, plan_index, cfg, cal)


def _apply_hazards(
    gen, cfg, panel, plan_index, churn_names, active, st, plan_id, m, dm, n_days, uid, spell_no, ev
):
    idx = np.flatnonzero(active)
    names = np.array([plan_index.row(int(p)).name for p in plan_id[idx]], dtype=object)

    churn_p = np.zeros(len(idx))
    for pname in churn_names:
        churn_p[names == pname] = panel.get(f"churn.{pname}")[dm]
    draw = gen.random(len(idx))
    churn = draw < churn_p
    ch_idx = idx[churn]
    if len(ch_idx):
        day = _month_day(gen, m, n_days, len(ch_idx))
        ev.add(
            uid[ch_idx],
            spell_no[ch_idx],
            "cancel",
            plan_id[ch_idx],
            0,
            day,
            gen.integers(0, 86400, size=len(ch_idx)),
        )
        st[ch_idx] = _CHURNED

    # non-churned: move one step along the paid-plan ladder (derived from config,
    # ordered by price). Upgrade -> next tier up; downgrade -> next tier down.
    survive = idx[~churn]
    if len(survive) == 0:
        return
    ladder = plan_index.paid_ladder
    if len(ladder) < 2:
        return  # single paid tier: nowhere to move
    tier_of = {n: i for i, n in enumerate(ladder)}
    tier = np.array([tier_of.get(plan_index.row(int(p)).name, 0) for p in plan_id[survive]])
    can_up = tier < len(ladder) - 1
    can_down = tier > 0
    up_p = panel.get("upgrade")[dm]
    down_p = panel.get("downgrade")[dm]
    upgrade = can_up & (gen.random(len(survive)) < up_p)
    downgrade = can_down & ~upgrade & (gen.random(len(survive)) < down_p)

    for direction, mask, step in (("upgrade", upgrade, 1), ("downgrade", downgrade, -1)):
        for k in np.flatnonzero(mask):
            j = survive[k]
            period = plan_index.row(int(plan_id[j])).billing_period
            to = plan_index.id_for(ladder[tier[k] + step], period)
            ev.add_one(
                uid[j], spell_no[j], direction, int(plan_id[j]), to, *_one_day(gen, m, n_days)
            )
            plan_id[j] = to


def _sample_plan(gen, plan_index: PlanIndex, names, probs, annual_share, size) -> np.ndarray:
    name_idx = gen.choice(len(names), size=size, p=probs)
    annual = gen.random(size) < annual_share
    out = np.empty(size, dtype=np.int64)
    for i in range(size):
        nm = names[name_idx[i]]
        period = "annual" if annual[i] and plan_index.has_period(nm, "annual") else "monthly"
        out[i] = plan_index.id_for(nm, period)
    return out


def _month_day(gen, m, n_days, size) -> np.ndarray:
    lo = m * _DAYS_PER_MONTH
    return np.clip(lo + gen.integers(0, _DAYS_PER_MONTH, size=size), 0, n_days - 1)


def _one_day(gen, m, n_days) -> tuple[int, int]:
    lo = m * _DAYS_PER_MONTH
    day = int(np.clip(lo + gen.integers(0, _DAYS_PER_MONTH), 0, n_days - 1))
    return day, int(gen.integers(0, 86400))


# --------------------------------------------------------------------------- #
# Event buffer + assembly
# --------------------------------------------------------------------------- #
class _EventBuffer:
    def __init__(self):
        self.user: list[np.ndarray] = []
        self.spell: list[np.ndarray] = []
        self.etype: list[np.ndarray] = []
        self.frm: list[np.ndarray] = []
        self.to: list[np.ndarray] = []
        self.day: list[np.ndarray] = []
        self.sec: list[np.ndarray] = []

    def add(self, user, spell, etype, frm, to, day, sec):
        # np.array (not asarray) forces a copy: callers pass live state arrays
        # (e.g. spell_no) that mutate later — a reference here would corrupt
        # already-recorded events.
        k = len(user)
        self.user.append(np.array(user, dtype=np.int64))
        self.spell.append(np.array(spell, dtype=np.int64))
        self.etype.append(np.full(k, etype, dtype=object))
        self.frm.append(
            np.full(k, frm, dtype=np.int64) if np.isscalar(frm) else np.array(frm, dtype=np.int64)
        )
        self.to.append(
            np.full(k, to, dtype=np.int64) if np.isscalar(to) else np.array(to, dtype=np.int64)
        )
        self.day.append(np.array(day, dtype=np.int64))
        self.sec.append(np.array(sec, dtype=np.int64))

    def add_one(self, user, spell, etype, frm, to, day, sec):
        self.add(
            np.array([user]), np.array([spell]), etype, frm, to, np.array([day]), np.array([sec])
        )

    def frame(self, cal: Calendar) -> pd.DataFrame:
        if not self.user:
            return pd.DataFrame(
                columns=[
                    "user_id",
                    "spell_no",
                    "event_type",
                    "from_plan_id",
                    "to_plan_id",
                    "occurred_day",
                    "occurred_at",
                ]
            )
        day = np.concatenate(self.day)
        sec = np.concatenate(self.sec)
        return (
            pd.DataFrame(
                {
                    "user_id": np.concatenate(self.user),
                    "spell_no": np.concatenate(self.spell),
                    "event_type": np.concatenate(self.etype),
                    "from_plan_id": np.concatenate(self.frm),
                    "to_plan_id": np.concatenate(self.to),
                    "occurred_day": day,
                    "occurred_at": timestamps_from_days(cal.start, day, sec),
                }
            )
            .sort_values("occurred_at", kind="stable")
            .reset_index(drop=True)
        )


def _finalize_events(events: pd.DataFrame, plan_index: PlanIndex) -> pd.DataFrame:
    if events.empty:
        return _empty_events()
    grp = events.groupby(["user_id", "spell_no"], sort=True).ngroup() + 1
    df = pd.DataFrame(
        {
            "event_id": np.arange(1, len(events) + 1, dtype=np.int64),
            "subscription_id": grp.to_numpy(),
            "user_id": events["user_id"].to_numpy(),
            "event_type": events["event_type"].to_numpy(),
            "from_plan_id": _nullable_plan(events["from_plan_id"]),
            "to_plan_id": _nullable_plan(events["to_plan_id"]),
            "occurred_at": events["occurred_at"].to_numpy(),
        }
    )
    # event_id ordered by occurrence time.
    df = df.sort_values("occurred_at", kind="stable").reset_index(drop=True)
    df["event_id"] = np.arange(1, len(df) + 1, dtype=np.int64)
    df["_loaded_at"] = pd.NaT
    return df


def _nullable_plan(series: pd.Series) -> pd.array:
    arr = series.to_numpy(dtype=np.int64)
    out = pd.array(arr, dtype="Int64")
    out[arr == 0] = pd.NA
    return out


def _derive_subscriptions(
    events: pd.DataFrame, plan_index: PlanIndex, cfg: ScenarioConfig, cal: Calendar
) -> pd.DataFrame:
    if events.empty:
        return _empty_subscriptions()
    e = events.copy()
    e["sub"] = e.groupby(["user_id", "spell_no"], sort=True).ngroup() + 1
    e = e.sort_values(["sub", "occurred_at"], kind="stable")

    activation = {"trial_convert", "new", "resurrect"}
    closing = {"cancel", "trial_expire"}

    rows = []
    for sub, g in e.groupby("sub", sort=True):
        etypes = g["event_type"].to_numpy()
        occ = g["occurred_at"].to_numpy()

        trial_start_at = _first_time(g, "trial_start")
        started_at = _first_time(g, list(activation))
        # current plan = last positive to_plan transition
        pos = g[g["to_plan_id"] > 0]
        plan = int(pos["to_plan_id"].to_numpy()[-1]) if len(pos) else 0

        last_type = etypes[-1]
        if last_type in closing:
            status = "canceled"
            canceled_at = occ[-1]
        elif started_at is not None:
            status = "active"
            canceled_at = _NAT
        else:
            status = "trialing"
            canceled_at = _NAT

        user_id = int(g["user_id"].to_numpy()[0])
        trial_end_at = (
            trial_start_at + np.timedelta64(cfg.lifecycle.trial_days, "D")
            if trial_start_at is not None
            else _NAT
        )
        rows.append(
            (
                sub,
                user_id,
                plan if plan > 0 else pd.NA,
                status,
                trial_start_at if trial_start_at is not None else _NAT,
                trial_end_at,
                started_at if started_at is not None else _NAT,
                canceled_at,
            )
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "subscription_id",
            "user_id",
            "plan_id",
            "status",
            "trial_start_at",
            "trial_end_at",
            "started_at",
            "canceled_at",
        ],
    )
    df["plan_id"] = df["plan_id"].astype("Int64")
    for c in ("trial_start_at", "trial_end_at", "started_at", "canceled_at"):
        df[c] = pd.to_datetime(df[c])
    df["_loaded_at"] = pd.NaT
    return df


def _first_time(g: pd.DataFrame, etype) -> np.datetime64 | None:
    types = etype if isinstance(etype, list) else [etype]
    hit = g[g["event_type"].isin(types)]
    if hit.empty:
        return None
    return hit["occurred_at"].to_numpy()[0]


def _empty_subscriptions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "subscription_id": pd.array([], dtype="int64"),
            "user_id": pd.array([], dtype="int64"),
            "plan_id": pd.array([], dtype="Int64"),
            "status": pd.array([], dtype="object"),
            "trial_start_at": pd.array([], dtype="datetime64[s]"),
            "trial_end_at": pd.array([], dtype="datetime64[s]"),
            "started_at": pd.array([], dtype="datetime64[s]"),
            "canceled_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": pd.array([], dtype="int64"),
            "subscription_id": pd.array([], dtype="int64"),
            "user_id": pd.array([], dtype="int64"),
            "event_type": pd.array([], dtype="object"),
            "from_plan_id": pd.array([], dtype="Int64"),
            "to_plan_id": pd.array([], dtype="Int64"),
            "occurred_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
