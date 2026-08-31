"""``ad_platform.ad_spend`` for the CPG brand — present but causally decoupled.

Spend follows the ``spend.<channel>`` drivers so a budget-cut anomaly is
visible in marketing_spend, and *only* there: nothing downstream reads these
drivers. Reuses the shared AD_SPEND table spec; the builder is CPG-local
because the shared one is written against the web verticals' traffic config.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ..config import CPGWholesaleScenarioConfig as ScenarioConfig

_CAMPAIGN_WEIGHTS = [0.6, 0.4]
_CTR = 0.015


def build_ad_spend(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub, panel: DriverPanel
) -> pd.DataFrame:
    gen = rng.stream("marketing")
    dates = cal.dates.date
    parts: list[pd.DataFrame] = []

    for ch, spec in cfg.marketing.channels.items():
        daily = panel.get(f"spend.{ch}")
        for ci, w in enumerate(_CAMPAIGN_WEIGHTS):
            camp_spend = daily * w
            clicks = gen.poisson(np.maximum(camp_spend / spec.cpc, 0.0)).astype(np.int64)
            impressions = np.round(clicks / _CTR).astype(np.int64)
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

    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values(["date", "channel", "campaign_id"]).reset_index(drop=True)
    df.insert(0, "spend_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT
    return df
