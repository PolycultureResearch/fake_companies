"""First orders drawn from sessions + the ``shop_db.customers`` frame.

Each session converts with probability ``conversion_rate.<channel>`` for its
day — overridden by a segment-specific driver when the session matches a
segmented anomaly (e.g. mobile-only checkout regression) — times a static
per-device multiplier. A converting session becomes a new customer and their
first order; the order inherits the session's channel/country/device.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ....latent.panel import driver_key
from ..config import RetailDTCScenarioConfig as ScenarioConfig


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


def build_first_orders(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    sessions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (order skeleton, customers frame).

    The skeleton has identity/timing columns only; basket composition and
    amounts are filled by the basket stage.
    """
    gen = rng.stream("retail_orders")
    n = len(sessions)
    if n == 0:
        return _empty_skeleton(), _empty_customers()

    day_idx = sessions["day_index"].to_numpy()
    channel = sessions["channel"].to_numpy()
    device = sessions["device"].to_numpy()

    # Base per-session conversion probability from the topline driver.
    prob = np.zeros(n)
    for ch in cfg.traffic.channels:
        arr = panel.get(f"conversion_rate.{ch}")
        m = channel == ch
        prob[m] = arr[day_idx[m]]

    # Segment overrides (invisible in topline until you slice) — same pattern
    # as the SaaS funnel, so segmented conversion anomalies materialize.
    for key in panel.rates:
        if "|" not in key or not key.startswith("conversion_rate."):
            continue
        name, seg_str = key.split("|", 1)
        ch = name.split(".", 1)[1]
        segment = dict(kv.split("=", 1) for kv in seg_str.split(","))
        seg_arr = panel.rates[driver_key(name, segment)]
        m = (channel == ch) & _segment_matches(sessions, segment)
        prob[m] = seg_arr[day_idx[m]]

    # Static device friction on top of the (possibly anomalized) driver.
    for dev, mult in cfg.conversion.device_multipliers.items():
        prob[device == dev] *= mult
    prob = np.clip(prob, 0.0, 1.0)

    converted = gen.random(n) < prob
    conv_idx = np.flatnonzero(converted)
    n_cust = len(conv_idx)
    if n_cust == 0:
        return _empty_skeleton(), _empty_customers()

    customer_ids = np.arange(1, n_cust + 1, dtype=np.int64)
    sessions.loc[sessions.index[conv_idx], "user_id"] = customer_ids

    s = sessions.iloc[conv_idx]
    placed_at = s["started_at"].to_numpy() + np.timedelta64(600, "s")  # ~10 min to checkout

    first_pool, last_pool = _name_pools(int(rng.stream("faker").integers(0, 2**32)))
    fi = gen.integers(0, len(first_pool), size=n_cust)
    li = gen.integers(0, len(last_pool), size=n_cust)
    first = np.asarray(first_pool, dtype=object)[fi]
    last = np.asarray(last_pool, dtype=object)[li]
    full_name = np.char.add(np.char.add(first.astype(str), " "), last.astype(str))
    email = [f"{f}.{l}{cid}@example.com".lower() for f, l, cid in zip(first, last, customer_ids)]

    skeleton = pd.DataFrame(
        {
            "customer_id": customer_ids,
            "session_id": s["session_id"].to_numpy(),
            "channel": s["channel"].to_numpy(),
            "country": s["country"].to_numpy(),
            "device": s["device"].to_numpy(),
            "is_first_order": True,
            "placed_at": placed_at,
            "day_index": day_idx[conv_idx],
        }
    )
    customers = pd.DataFrame(
        {
            "customer_id": customer_ids,
            "email": email,
            "full_name": full_name,
            "country": s["country"].to_numpy(),
            "first_channel": s["channel"].to_numpy(),
            "first_device": s["device"].to_numpy(),
            "created_at": placed_at,
            "_loaded_at": pd.NaT,
        }
    )
    return skeleton, customers


def _empty_skeleton() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": pd.array([], dtype="int64"),
            "session_id": pd.array([], dtype="Int64"),
            "channel": pd.array([], dtype="object"),
            "country": pd.array([], dtype="object"),
            "device": pd.array([], dtype="object"),
            "is_first_order": pd.array([], dtype="bool"),
            "placed_at": pd.array([], dtype="datetime64[s]"),
            "day_index": pd.array([], dtype="int64"),
        }
    )


def _empty_customers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": pd.array([], dtype="int64"),
            "email": pd.array([], dtype="object"),
            "full_name": pd.array([], dtype="object"),
            "country": pd.array([], dtype="object"),
            "first_channel": pd.array([], dtype="object"),
            "first_device": pd.array([], dtype="object"),
            "created_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
