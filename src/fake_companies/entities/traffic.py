"""``web.sessions`` — the big session table drawn from latent rates.

Per channel per day, session counts are Poisson-distributed around the channel's
latent rate: paid channels use ``spend.<channel>`` / cpc (so spend cuts cascade),
organic/fixed channels use their ``sessions.<channel>`` driver directly. Rows are
expanded vectorized, timestamps follow a diurnal shape, and country/device are
sampled from the configured mix.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel
from ._util import (
    SESSION_HOUR_WEIGHTS,
    expand_day_index,
    intraday_seconds,
    lognormal_around,
    poisson_counts,
    sample_labels,
    timestamps_from_days,
)

_LANDING_PAGES = ["/", "/pricing", "/features", "/blog", "/signup", "/product"]


def channel_daily_rate(cfg: ScenarioConfig, panel: DriverPanel, channel: str) -> np.ndarray:
    """Expected sessions/day for a channel (paid = spend/cpc; else direct)."""
    spec = cfg.traffic.channels[channel]
    if spec.kind == "paid":
        cpc = float(spec.cpc)  # type: ignore[arg-type]
        return panel.get(f"spend.{channel}") / cpc
    return panel.get(f"sessions.{channel}")


def build_sessions(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub, panel: DriverPanel
) -> pd.DataFrame:
    gen = rng.stream("traffic")
    countries = list(cfg.mix.country)
    country_p = list(cfg.mix.country.values())
    devices = list(cfg.mix.device)
    device_p = list(cfg.mix.device.values())

    chan_frames: list[pd.DataFrame] = []
    for ch in cfg.traffic.channels:
        rates = channel_daily_rate(cfg, panel, ch)
        counts = poisson_counts(gen, rates)
        total = int(counts.sum())
        if total == 0:
            continue
        day_idx = expand_day_index(counts)
        secs = intraday_seconds(gen, day_idx, SESSION_HOUR_WEIGHTS)
        started_at = timestamps_from_days(cal.start, day_idx, secs)
        chan_frames.append(
            pd.DataFrame(
                {
                    "day_index": day_idx,
                    "channel": ch,
                    "country": sample_labels(gen, countries, country_p, total),
                    "device": sample_labels(gen, devices, device_p, total),
                    "started_at": started_at,
                    "duration_seconds": np.maximum(
                        1,
                        lognormal_around(gen, cfg.traffic.duration_seconds_mean, 0.9, total),
                    ).astype(np.int32),
                    "page_views": (
                        1 + gen.poisson(max(cfg.traffic.page_views_mean - 1, 0.1), size=total)
                    ).astype(np.int32),
                    "landing_page": sample_labels(
                        gen, _LANDING_PAGES, [1] * len(_LANDING_PAGES), total
                    ),
                }
            )
        )

    if not chan_frames:
        return _empty_sessions()

    df = pd.concat(chan_frames, ignore_index=True)
    df = df.sort_values("started_at", kind="stable").reset_index(drop=True)
    n = len(df)
    df.insert(0, "session_id", np.arange(1, n + 1, dtype=np.int64))
    df["anonymous_id"] = [f"anon_{i:09d}" for i in range(1, n + 1)]
    df["user_id"] = pd.array([pd.NA] * n, dtype="Int64")  # set for signup sessions in funnel
    utm = np.where(
        df["channel"].isin(["paid_search", "paid_social", "display"]),
        df["channel"].astype(str) + "_camp",
        None,
    )
    df["utm_campaign"] = utm
    df["_loaded_at"] = pd.NaT
    return df


def _empty_sessions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "session_id": pd.array([], dtype="int64"),
            "anonymous_id": pd.array([], dtype="object"),
            "user_id": pd.array([], dtype="Int64"),
            "channel": pd.array([], dtype="object"),
            "utm_campaign": pd.array([], dtype="object"),
            "country": pd.array([], dtype="object"),
            "device": pd.array([], dtype="object"),
            "started_at": pd.array([], dtype="datetime64[s]"),
            "duration_seconds": pd.array([], dtype="int32"),
            "page_views": pd.array([], dtype="int32"),
            "landing_page": pd.array([], dtype="object"),
            "day_index": pd.array([], dtype="int64"),
        }
    )
