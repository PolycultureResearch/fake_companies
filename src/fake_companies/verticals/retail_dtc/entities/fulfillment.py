"""``fulfillment.shipments`` — one shipment per order with lognormal lags.

``shipped_at = placed_at + handling``, ``delivered_at = shipped_at + transit``.
Timestamps past the timeline end are right-censored to NULL and the status
reflects how far the shipment got by the cutoff (pending/shipped/delivered).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....shared._util import lognormal_around, sample_labels
from ..config import RetailDTCScenarioConfig as ScenarioConfig


def build_shipments(
    cfg: ScenarioConfig, cal: Calendar, rng: RngHub, orders: pd.DataFrame
) -> pd.DataFrame:
    gen = rng.stream("retail_fulfillment")
    n = len(orders)
    fc = cfg.fulfillment

    placed = pd.to_datetime(orders["placed_at"]).to_numpy() if n else np.array([], "datetime64[s]")
    handling_s = (
        lognormal_around(gen, fc.handling_days_mean, fc.handling_sigma, n) * 86400.0
    ).astype("int64")
    transit_s = (lognormal_around(gen, fc.transit_days_mean, fc.transit_sigma, n) * 86400.0).astype(
        "int64"
    )
    shipped = placed + handling_s.astype("timedelta64[s]")
    delivered = shipped + transit_s.astype("timedelta64[s]")

    cutoff = np.datetime64(cal.end, "s") + np.timedelta64(86399, "s")
    shipped_ok = shipped <= cutoff
    delivered_ok = delivered <= cutoff
    status = np.where(delivered_ok, "delivered", np.where(shipped_ok, "shipped", "pending"))

    carriers = list(fc.carrier_mix)
    carrier_p = list(fc.carrier_mix.values())

    df = pd.DataFrame(
        {
            "shipment_id": np.arange(1, n + 1, dtype=np.int64),
            "order_id": orders["order_id"].to_numpy() if n else np.array([], dtype=np.int64),
            "carrier": sample_labels(gen, carriers, carrier_p, n),
            "status": status,
            "placed_at": placed,
            "shipped_at": pd.Series(shipped).where(pd.Series(shipped_ok), pd.NaT).to_numpy(),
            "delivered_at": pd.Series(delivered).where(pd.Series(delivered_ok), pd.NaT).to_numpy(),
            "_loaded_at": pd.NaT,
        }
    )
    return df
