from __future__ import annotations

import pandas as pd
import pytest

from fake_companies.config import load_config
from fake_companies.generate import generate


@pytest.fixture(scope="module")
def smoke_run():
    cfg = load_config("configs/smoke_90d.yaml")
    return cfg, generate(cfg)


def test_fk_integrity(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    pay = r.frames["billing.payments"]
    subs = r.frames["app_db.subscriptions"]
    assert set(pay["invoice_id"]) <= set(inv["invoice_id"])
    assert set(inv["subscription_id"]) <= set(subs["subscription_id"])


def test_paid_subscriptions_have_invoices(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    subs = r.frames["app_db.subscriptions"]

    # Paid spells whose active window spans at least one 30-day period.
    paid = subs[subs["started_at"].notna() & subs["plan_id"].notna()].copy()
    end = pd.to_datetime(paid["canceled_at"]).fillna(
        pd.Timestamp(r.calendar.end) + pd.Timedelta(days=1)
    )
    span_days = (end - pd.to_datetime(paid["started_at"])).dt.total_seconds() / 86400.0
    spanning = paid.loc[span_days >= 30.0, "subscription_id"]

    invoiced = set(inv["subscription_id"])
    assert set(spanning) <= invoiced


def test_every_invoice_has_a_payment(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    pay = r.frames["billing.payments"]
    assert set(inv["invoice_id"]) <= set(pay["invoice_id"])


def test_payment_failure_rate_in_band(smoke_run):
    _, r = smoke_run
    pay = r.frames["billing.payments"]
    rate = (pay["status"] == "failed").mean()
    assert 0.02 <= rate <= 0.09, rate


def test_currency_distribution(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    counts = inv["currency"].value_counts()
    assert counts.idxmax() == "USD"
    assert (counts.index != "USD").any()


def test_amounts_positive_and_ids_unique(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    pay = r.frames["billing.payments"]
    assert (inv["amount"] > 0).all()
    assert (pay["amount"] > 0).all()
    assert inv["invoice_id"].is_unique
    assert pay["payment_id"].is_unique


def test_invoice_status_matches_payments(smoke_run):
    _, r = smoke_run
    inv = r.frames["billing.invoices"]
    pay = r.frames["billing.payments"]
    assert set(inv["status"]).issubset({"paid", "open"})
    succeeded = set(pay.loc[pay["status"] == "succeeded", "invoice_id"])
    paid_ids = set(inv.loc[inv["status"] == "paid", "invoice_id"])
    assert paid_ids == succeeded


def test_failure_code_nullity(smoke_run):
    _, r = smoke_run
    pay = r.frames["billing.payments"]
    ok = pay["status"] == "succeeded"
    assert pay.loc[ok, "failure_code"].isna().all()
    assert pay.loc[~ok, "failure_code"].notna().all()
