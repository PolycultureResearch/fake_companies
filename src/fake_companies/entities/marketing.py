"""``ad_platform.ad_spend`` — daily spend per paid channel/campaign.

Spend follows the ``spend.<channel>`` latent driver (so a budget-cut anomaly
shows here). Clicks/impressions are drawn around spend so the platform-reported
funnel is internally consistent with the site sessions derived from the same
driver.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ..latent import DriverPanel

_CAMPAIGN_WEIGHTS = [0.6, 0.4]
_CTR = {"paid_search": 0.045, "paid_social": 0.012, "display": 0.004}


def build_ad_spend(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub, panel: DriverPanel
) -> pd.DataFrame:
    gen = rng.stream("marketing")
    dates = cal.dates.date
    parts: list[pd.DataFrame] = []

    for ch, spec in cfg.traffic.channels.items():
        if spec.kind != "paid":
            continue
        cpc = float(spec.cpc)  # type: ignore[arg-type]
        ctr = _CTR.get(ch, 0.02)
        daily = panel.get(f"spend.{ch}")
        for ci, w in enumerate(_CAMPAIGN_WEIGHTS):
            camp_spend = daily * w
            clicks = gen.poisson(np.maximum(camp_spend / cpc, 0.0)).astype(np.int64)
            impressions = np.round(clicks / ctr).astype(np.int64)
            parts.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "channel": ch,
                        "campaign_id": f"{ch}_camp_{ci + 1}",
                        "impressions": impressions,
                        "clicks": clicks,
                        "spend": np.round(camp_spend, 2),
                        "currency": cfg.company.currency,
                    }
                )
            )

    if not parts:
        df = pd.DataFrame(
            columns=["date", "channel", "campaign_id", "impressions", "clicks", "spend", "currency"]
        )
    else:
        df = pd.concat(parts, ignore_index=True)
        df = df.sort_values(["date", "channel", "campaign_id"]).reset_index(drop=True)

    df.insert(0, "spend_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT  # filled by the loading model in M4
    return df
