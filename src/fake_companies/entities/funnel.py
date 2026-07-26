"""``app_db.users`` — signups drawn from sessions via per-session conversion.

Each session converts with probability ``signup_rate.<channel>`` for its day,
overridden by a segment-specific driver when the session matches a segmented
anomaly (e.g. US-mobile paid_social). Converting sessions get a ``user_id``; the
user inherits the session's channel/country/device. A ``started_trial`` flag
(drawn from ``trial_start_rate``) is carried for the lifecycle stage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ..latent.panel import driver_key


def _name_pools(seed_int: int) -> tuple[list[str], list[str]]:
    from faker import Faker

    fake = Faker()
    Faker.seed(seed_int)
    first = [fake.first_name() for _ in range(2000)]
    last = [fake.last_name() for _ in range(2000)]
    return first, last


def _segment_matches(sessions: pd.DataFrame, segment: dict[str, str]) -> np.ndarray:
    mask = np.ones(len(sessions), dtype=bool)
    for dim, val in segment.items():
        if dim in sessions.columns:
            mask &= sessions[dim].to_numpy() == val
    return mask


def build_users(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    sessions: pd.DataFrame,
) -> pd.DataFrame:
    gen = rng.stream("funnel")
    n = len(sessions)
    if n == 0:
        return _empty_users()

    day_idx = sessions["day_index"].to_numpy()
    channel = sessions["channel"].to_numpy()

    # Base per-session conversion probability from the topline driver.
    prob = np.zeros(n)
    for ch in cfg.traffic.channels:
        arr = panel.get(f"signup_rate.{ch}")
        m = channel == ch
        prob[m] = arr[day_idx[m]]

    # Segment overrides (invisible in topline until you slice).
    for key in panel.rates:
        if "|" not in key or not key.startswith("signup_rate."):
            continue
        name, seg_str = key.split("|", 1)
        ch = name.split(".", 1)[1]
        segment = dict(kv.split("=", 1) for kv in seg_str.split(","))
        seg_arr = panel.rates[driver_key(name, segment)]
        m = (channel == ch) & _segment_matches(sessions, segment)
        prob[m] = seg_arr[day_idx[m]]

    signup = gen.random(n) < prob
    signup_idx = np.flatnonzero(signup)
    n_users = len(signup_idx)
    if n_users == 0:
        return _empty_users()

    user_ids = np.arange(1, n_users + 1, dtype=np.int64)
    sessions.loc[sessions.index[signup_idx], "user_id"] = user_ids

    s = sessions.iloc[signup_idx]
    created_at = s["started_at"].to_numpy()
    u_day = day_idx[signup_idx]

    first_pool, last_pool = _name_pools(int(rng.stream("faker").integers(0, 2**32)))
    fi = gen.integers(0, len(first_pool), size=n_users)
    li = gen.integers(0, len(last_pool), size=n_users)
    first = np.asarray(first_pool, dtype=object)[fi]
    last = np.asarray(last_pool, dtype=object)[li]
    full_name = np.char.add(np.char.add(first.astype(str), " "), last.astype(str))
    email = [f"{f}.{l}{uid}@example.com".lower() for f, l, uid in zip(first, last, user_ids)]

    trial_rate = panel.get("trial_start_rate")[u_day]
    started_trial = gen.random(n_users) < trial_rate

    users = pd.DataFrame(
        {
            "user_id": user_ids,
            "email": email,
            "full_name": full_name,
            "country": s["country"].to_numpy(),
            "signup_channel": s["channel"].to_numpy(),
            "device_at_signup": s["device"].to_numpy(),
            "created_at": created_at,
            "_loaded_at": pd.NaT,
            # --- carried for the lifecycle stage (dropped on write) ---
            "day_index": u_day,
            "started_trial": started_trial,
        }
    )
    return users


def _empty_users() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": pd.array([], dtype="int64"),
            "email": pd.array([], dtype="object"),
            "full_name": pd.array([], dtype="object"),
            "country": pd.array([], dtype="object"),
            "signup_channel": pd.array([], dtype="object"),
            "device_at_signup": pd.array([], dtype="object"),
            "created_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
            "day_index": pd.array([], dtype="int64"),
            "started_trial": pd.array([], dtype="bool"),
        }
    )
