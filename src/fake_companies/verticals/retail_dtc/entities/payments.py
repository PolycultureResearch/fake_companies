"""``payments.transactions`` — charges per order + refunds from returns.

Every order is charged shortly after placement; a configurable share of first
attempts fails (leaving a failed row with a failure code) and is retried
successfully within a day — the same observable pattern as SaaS dunning,
simplified to one retry. Each refunded return emits a refund transaction at
its ``refunded_at``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....shared._util import lognormal_around, sample_labels
from ..config import RetailDTCScenarioConfig as ScenarioConfig


def build_transactions(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    orders: pd.DataFrame,
    returns: pd.DataFrame,
) -> pd.DataFrame:
    gen = rng.stream("retail_payments")
    pc = cfg.payments
    n = len(orders)

    methods = list(pc.method_mix)
    method_p = list(pc.method_mix.values())
    codes = list(pc.failure_codes)
    code_p = list(pc.failure_codes.values())

    placed = pd.to_datetime(orders["placed_at"]).to_numpy()
    method = sample_labels(gen, methods, method_p, n)
    amount = orders["total_amount"].to_numpy()
    order_id = orders["order_id"].to_numpy()

    charge_lag = (lognormal_around(gen, 2.0, 0.4, n) * 60.0).astype("int64")  # ~2 min
    charge_at = placed + charge_lag.astype("timedelta64[s]")

    failed_first = gen.random(n) < pc.failure_rate
    fail_idx = np.flatnonzero(failed_first)
    retry_lag = (lognormal_around(gen, 6.0, 0.6, len(fail_idx)) * 3600.0).astype(
        "int64"
    )  # ~6 h retry

    parts: list[pd.DataFrame] = []
    # Failed first attempts.
    if len(fail_idx):
        parts.append(
            pd.DataFrame(
                {
                    "order_id": order_id[fail_idx],
                    "kind": "charge",
                    "amount": amount[fail_idx],
                    "currency": cfg.company.currency,
                    "payment_method": method[fail_idx],
                    "status": "failed",
                    "failure_code": sample_labels(gen, codes, code_p, len(fail_idx)),
                    "created_at": charge_at[fail_idx],
                }
            )
        )
    # Successful charges (first attempt, or the retry for failed ones).
    success_at = charge_at.copy()
    if len(fail_idx):
        success_at[fail_idx] = charge_at[fail_idx] + retry_lag.astype("timedelta64[s]")
    parts.append(
        pd.DataFrame(
            {
                "order_id": order_id,
                "kind": "charge",
                "amount": amount,
                "currency": cfg.company.currency,
                "payment_method": method,
                "status": "succeeded",
                "failure_code": None,
                "created_at": success_at,
            }
        )
    )
    # Refunds from refunded returns.
    refunded = returns[returns["refunded_at"].notna()]
    if len(refunded):
        order_method = pd.Series(method, index=order_id)
        parts.append(
            pd.DataFrame(
                {
                    "order_id": refunded["order_id"].to_numpy(),
                    "kind": "refund",
                    "amount": refunded["refund_amount"].to_numpy(),
                    "currency": cfg.company.currency,
                    "payment_method": order_method.reindex(refunded["order_id"]).to_numpy(),
                    "status": "succeeded",
                    "failure_code": None,
                    "created_at": pd.to_datetime(refunded["refunded_at"]).to_numpy(),
                }
            )
        )

    df = pd.concat(parts, ignore_index=True)
    df = df.sort_values("created_at", kind="stable").reset_index(drop=True)
    df.insert(0, "payment_id", np.arange(1, len(df) + 1, dtype=np.int64))
    df["_loaded_at"] = pd.NaT
    return df
