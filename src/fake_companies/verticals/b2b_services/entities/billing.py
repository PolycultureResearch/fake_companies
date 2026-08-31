"""Contracts, invoices, and payments for closed-won deals.

A won deal signs a contract at close. Billing is milestone-shaped: an upfront
invoice at signing and a balance invoice at delivery, each net-N with a share
paid late. Everything past the timeline end is censored — delivery not yet
reached, invoices not yet issued, open invoices unpaid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ....core import RngHub
from ....core.calendar import Calendar
from ....shared._util import lognormal_around, sample_labels
from ..config import B2BServicesScenarioConfig as ScenarioConfig

_METHODS = ["ach", "wire", "check"]
_METHOD_P = [0.6, 0.25, 0.15]


def build_billing(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    deals: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> None:
    gen = rng.stream("b2b_billing")
    cc = cfg.contracts
    cutoff = np.datetime64(cal.end, "s") + np.timedelta64(86399, "s")

    won = deals[deals["won"]].reset_index(drop=True)
    n = len(won)

    signed_at = pd.to_datetime(won["closed_at"]).to_numpy()
    delivery_s = (
        lognormal_around(gen, cc.delivery_days_mean, cc.delivery_sigma, n) * 86400
    ).astype("int64")
    delivery_at = signed_at + delivery_s.astype("timedelta64[s]")
    delivered = delivery_at <= cutoff

    contract_ids = np.arange(1, n + 1, dtype=np.int64)
    frames["billing.contracts"] = pd.DataFrame(
        {
            "contract_id": contract_ids,
            "deal_id": won["deal_id"].to_numpy(),
            "account_id": won["account_id"].to_numpy(),
            "value": won["amount"].to_numpy(),
            "currency": cfg.company.currency,
            "signed_at": signed_at,
            "delivery_at": pd.Series(delivery_at).where(pd.Series(delivered), pd.NaT).to_numpy(),
            "_loaded_at": pd.NaT,
        }
    )

    # --- invoices: upfront at signing, balance at delivery -------------------- #
    value = won["amount"].to_numpy()
    upfront = np.round(value * cc.upfront_share, 2)
    balance = np.round(value - upfront, 2)

    inv_parts = [
        pd.DataFrame(
            {
                "contract_id": contract_ids,
                "account_id": won["account_id"].to_numpy(),
                "kind": "upfront",
                "amount": upfront,
                "issued_at": signed_at,
            }
        ),
        pd.DataFrame(
            {
                "contract_id": contract_ids[delivered],
                "account_id": won["account_id"].to_numpy()[delivered],
                "kind": "balance",
                "amount": balance[delivered],
                "issued_at": delivery_at[delivered],
            }
        ),
    ]
    inv = pd.concat(inv_parts, ignore_index=True)
    inv = inv.sort_values(["issued_at", "contract_id"], kind="stable").reset_index(drop=True)
    m = len(inv)
    inv.insert(0, "invoice_id", np.arange(1, m + 1, dtype=np.int64))
    issued = pd.to_datetime(inv["issued_at"]).to_numpy()
    due = issued + np.timedelta64(cc.net_days * 86400, "s")

    # Payment timing: mostly inside terms, a configured share late.
    within_frac = gen.uniform(0.3, 1.0, size=m)
    pay_offset_s = (within_frac * cc.net_days * 86400).astype("int64")
    late = gen.random(m) < cc.late_rate
    late_extra_s = (lognormal_around(gen, cc.late_days_mean, cc.late_sigma, m) * 86400).astype(
        "int64"
    )
    paid_at = issued + pay_offset_s.astype("timedelta64[s]")
    paid_at[late] = due[late] + late_extra_s[late].astype("timedelta64[s]")
    paid_observed = paid_at <= cutoff

    inv["currency"] = cfg.company.currency
    inv["status"] = np.where(paid_observed, "paid", "open")
    inv["due_at"] = due
    inv["_loaded_at"] = pd.NaT
    frames["billing.invoices"] = inv[
        [
            "invoice_id",
            "contract_id",
            "account_id",
            "kind",
            "amount",
            "currency",
            "status",
            "issued_at",
            "due_at",
            "_loaded_at",
        ]
    ]

    pm = paid_observed
    pay = pd.DataFrame(
        {
            "invoice_id": inv.loc[pm, "invoice_id"].to_numpy(),
            "account_id": inv.loc[pm, "account_id"].to_numpy(),
            "amount": inv.loc[pm, "amount"].to_numpy(),
            "currency": cfg.company.currency,
            "method": sample_labels(gen, _METHODS, _METHOD_P, int(pm.sum())),
            "paid_at": paid_at[pm],
        }
    )
    pay = pay.sort_values(["paid_at", "invoice_id"], kind="stable").reset_index(drop=True)
    pay.insert(0, "payment_id", np.arange(1, len(pay) + 1, dtype=np.int64))
    pay["_loaded_at"] = pd.NaT
    frames["billing.payments"] = pay
