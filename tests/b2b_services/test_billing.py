from __future__ import annotations

import numpy as np
import pandas as pd


def test_contracts_match_won_deals(b2b_result):
    deals = b2b_result.frames["crm.deals"].drop_duplicates("deal_id")
    contracts = b2b_result.frames["billing.contracts"]
    won = deals[deals["stage"] == "closed_won"]
    assert set(contracts["deal_id"]) == set(won["deal_id"])
    joined = contracts.merge(won, on="deal_id")
    assert np.allclose(joined["value"], joined["amount"])
    assert (
        pd.to_datetime(joined["signed_at"]).to_numpy()
        == pd.to_datetime(joined["closed_at"]).to_numpy()
    ).all()


def test_invoices_cover_contract_value(b2b_cfg, b2b_result):
    contracts = b2b_result.frames["billing.contracts"]
    invoices = b2b_result.frames["billing.invoices"].drop_duplicates("invoice_id")

    # Every contract has an upfront invoice at signing.
    upfront = invoices[invoices["kind"] == "upfront"]
    assert set(upfront["contract_id"]) == set(contracts["contract_id"])
    share = b2b_cfg.contracts.upfront_share
    joined = upfront.merge(contracts, on="contract_id")
    assert np.allclose(joined["amount"], joined["value"] * share, atol=0.02)

    # Delivered contracts also have the balance invoice; the two sum to value.
    delivered = contracts[contracts["delivery_at"].notna()]
    balance = invoices[invoices["kind"] == "balance"]
    assert set(balance["contract_id"]) == set(delivered["contract_id"])
    totals = invoices.groupby("contract_id")["amount"].sum()
    d = delivered.set_index("contract_id")
    assert np.allclose(totals.reindex(d.index), d["value"], atol=0.02)


def test_payments_match_paid_invoices(b2b_result):
    invoices = b2b_result.frames["billing.invoices"].drop_duplicates("invoice_id")
    payments = b2b_result.frames["billing.payments"]
    cutoff = pd.Timestamp(b2b_result.calendar.end) + pd.Timedelta(days=1)

    paid = invoices[invoices["status"] == "paid"]
    assert set(payments["invoice_id"]) == set(paid["invoice_id"])
    joined = payments.merge(invoices, on="invoice_id", suffixes=("", "_inv"))
    assert np.allclose(joined["amount"], joined["amount_inv"])
    paid_at = pd.to_datetime(joined["paid_at"])
    assert (paid_at > pd.to_datetime(joined["issued_at"])).all()
    assert (paid_at < cutoff).all()
