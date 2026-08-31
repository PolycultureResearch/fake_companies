"""``shop_db.returns`` — per-line Bernoulli returns with delayed observation.

A delivered line is returned with probability ``return_rate.<category>`` for
its order day. The request lags delivery by a lognormal delay and the refund
lags the request — anomalies on return rates therefore surface as genuinely
delayed signals downstream, and requests past the timeline end are censored
(the return simply hasn't happened yet in the observed data).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....latent import DriverPanel
from ....shared._util import lognormal_around, sample_labels
from ..config import RetailDTCScenarioConfig as ScenarioConfig
from .catalog import SkuCatalog


def build_returns(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    panel: DriverPanel,
    orders: pd.DataFrame,
    items: pd.DataFrame,
    shipments: pd.DataFrame,
    catalog: SkuCatalog,
) -> pd.DataFrame:
    gen = rng.stream("retail_returns")
    rc = cfg.returns

    delivered_at = shipments.set_index("order_id")["delivered_at"]
    line_delivered = pd.to_datetime(items["order_id"].map(delivered_at))
    eligible = line_delivered.notna().to_numpy()

    order_day = (
        (pd.to_datetime(items["placed_at"]) - pd.Timestamp(cal.start)).dt.days.to_numpy().clip(0)
    )

    # Per-line return probability from the per-category driver (order-day rate:
    # the sizing/quality problem ships with the product, not the return date).
    cat_to_idx = {c: i for i, c in enumerate(catalog.categories)}
    cat_idx = items["category"].map(cat_to_idx).to_numpy()
    rate_matrix = np.stack(
        [panel.get(f"return_rate.{c}") for c in catalog.categories], axis=1
    )  # (n_days, n_cats)
    p = rate_matrix[order_day, cat_idx]

    hit = eligible & (gen.random(len(items)) < p)
    idx = np.flatnonzero(hit)
    if len(idx) == 0:
        return _empty_returns()

    lines = items.iloc[idx]
    delivered = line_delivered.to_numpy()[idx]

    delay_s = (
        lognormal_around(gen, rc.request_delay_days_mean, rc.request_delay_sigma, len(idx)) * 86400
    ).astype("int64")
    requested = delivered + delay_s.astype("timedelta64[s]")

    cutoff = np.datetime64(cal.end, "s") + np.timedelta64(86399, "s")
    observed = requested <= cutoff
    if not observed.any():
        return _empty_returns()
    lines = lines.iloc[observed]
    requested = requested[observed]
    n = len(lines)

    processing_s = (
        lognormal_around(gen, rc.processing_days_mean, rc.processing_sigma, n) * 86400
    ).astype("int64")
    refunded = requested + processing_s.astype("timedelta64[s]")
    refunded_ok = refunded <= cutoff

    # Refund the line net of its share of any order-level discount.
    o = orders.set_index("order_id")
    gross = o["gross_amount"].reindex(lines["order_id"]).to_numpy()
    disc = o["discount_amount"].reindex(lines["order_id"]).to_numpy()
    disc_rate = np.divide(disc, gross, out=np.zeros_like(disc), where=gross > 0)
    refund_amount = np.round(lines["line_amount"].to_numpy() * (1.0 - disc_rate), 2)

    reasons = list(rc.reason_mix)
    reason_p = list(rc.reason_mix.values())

    df = pd.DataFrame(
        {
            "order_id": lines["order_id"].to_numpy(),
            "order_item_id": lines["order_item_id"].to_numpy(),
            "sku_id": lines["sku_id"].to_numpy(),
            "category": lines["category"].to_numpy(),
            "quantity": lines["quantity"].to_numpy(),
            "reason": sample_labels(gen, reasons, reason_p, n),
            "refund_amount": refund_amount,
            "requested_at": requested,
            "refunded_at": pd.Series(refunded).where(pd.Series(refunded_ok), pd.NaT).to_numpy(),
            "_loaded_at": pd.NaT,
        }
    )
    df = df.sort_values("requested_at", kind="stable").reset_index(drop=True)
    df.insert(0, "return_id", np.arange(1, n + 1, dtype=np.int64))
    return df


def _empty_returns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "return_id": pd.array([], dtype="int64"),
            "order_id": pd.array([], dtype="int64"),
            "order_item_id": pd.array([], dtype="int64"),
            "sku_id": pd.array([], dtype="int64"),
            "category": pd.array([], dtype="object"),
            "quantity": pd.array([], dtype="int32"),
            "reason": pd.array([], dtype="object"),
            "refund_amount": pd.array([], dtype="float64"),
            "requested_at": pd.array([], dtype="datetime64[s]"),
            "refunded_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
