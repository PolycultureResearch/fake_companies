"""``billing.invoices`` + ``billing.payments`` — invoicing and dunning.

Every paid subscription spell (``started_at`` not null) is billed from its paid
start until it cancels (or the timeline end for still-active spells). Each spell
uses a single plan for all of its invoices — a documented simplification, since
mid-spell plan changes are rare. Monthly plans are billed in ~30-day periods,
annual plans in 365-day periods, upfront at each period start.

``payments`` is the Tremor dataflow star table: one first attempt per invoice
(~96.5% succeed), and a short dunning tail of 1-2 retries on failures (each
~75% likely to clear). The blend lands the overall payment failure rate around
3-5%. An invoice is ``paid`` once any of its payments succeeds, else ``open``.

All randomness flows from ``rng.stream("billing")``; the module never touches
``np.random`` directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ScenarioConfig
from ..core import RngHub
from ..core.calendar import Calendar
from ._util import lognormal_around, sample_labels
from .plans import PlanIndex

_PERIOD_DAYS = {"monthly": 30, "annual": 365}

# Country -> billing currency (falls back to the company default otherwise).
_CURRENCY_BY_COUNTRY = {
    "US": "USD",
    "CA": "USD",
    "AU": "USD",
    "IN": "USD",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
}

_FAILURE_CODES = ["insufficient_funds", "card_declined", "expired_card", "processing_error"]
_METHODS = ["card", "paypal", "bank_transfer"]
_METHOD_WEIGHTS = [0.80, 0.15, 0.05]

_P_FIRST_SUCCESS = 0.975  # first-attempt success probability
_P_RETRY_SUCCESS = 0.80  # per-retry success probability
_MAX_RETRIES = 2  # dunning attempts after a failed first charge

_FIRST_DELAY_MEAN_S = 3600.0  # authorization lag: ~1h (minutes-hours)
_FIRST_DELAY_SIGMA = 1.0
_RETRY_DELAY_MEAN_S = 3 * 86400.0  # dunning retries land a few days apart
_RETRY_DELAY_SIGMA = 0.5


def build_billing(
    cfg: ScenarioConfig,
    cal: Calendar,
    rng: RngHub,
    plan_index: PlanIndex,
    frames: dict[str, pd.DataFrame],
) -> None:
    subs = frames["app_db.subscriptions"]
    users = frames["app_db.users"]
    gen = rng.stream("billing")

    paid = subs[subs["started_at"].notna() & subs["plan_id"].notna()]
    if len(paid) == 0:
        frames["billing.invoices"] = _empty_invoices()
        frames["billing.payments"] = _empty_payments()
        return

    invoices, inv_pos = _build_invoices(cfg, cal, paid, users, plan_index)
    payments, inv_paid = _build_payments(gen, invoices)

    invoices["status"] = np.where(inv_paid, "paid", "open")
    frames["billing.invoices"] = invoices
    frames["billing.payments"] = payments
    del inv_pos  # positions are internal to _build_payments


def _build_invoices(
    cfg: ScenarioConfig,
    cal: Calendar,
    paid: pd.DataFrame,
    users: pd.DataFrame,
    plan_index: PlanIndex,
) -> tuple[pd.DataFrame, np.ndarray]:
    sub_arr = paid["subscription_id"].to_numpy(dtype=np.int64)
    user_arr = paid["user_id"].to_numpy(dtype=np.int64)
    plan_arr = paid["plan_id"].astype("int64").to_numpy()
    started = pd.to_datetime(paid["started_at"]).to_numpy().astype("datetime64[s]")
    canceled = pd.to_datetime(paid["canceled_at"]).to_numpy().astype("datetime64[s]")

    # Plan attributes via small lookup tables indexed by plan_id (fully vectorized).
    max_pid = max(r.plan_id for r in plan_index.rows)
    price_lut = np.zeros(max_pid + 1, dtype=float)
    period_lut = np.zeros(max_pid + 1, dtype=np.int64)
    for r in plan_index.rows:
        price_lut[r.plan_id] = r.price
        period_lut[r.plan_id] = _PERIOD_DAYS[r.billing_period]
    price_arr = price_lut[plan_arr]
    period_s = period_lut[plan_arr] * 86400

    # Currency from the subscriber's country.
    country_by_user = users.drop_duplicates("user_id").set_index("user_id")["country"]
    country = paid["user_id"].map(country_by_user)
    currency_arr = (
        country.map(_CURRENCY_BY_COUNTRY).fillna(cfg.company.currency).to_numpy(dtype=object)
    )

    # Active window: [started_at, canceled_at or end-of-timeline).
    timeline_end = np.datetime64(cal.end).astype("datetime64[s]") + np.timedelta64(86400, "s")
    end = np.where(np.isnat(canceled), timeline_end, canceled)
    dur_s = np.maximum((end - started).astype("timedelta64[s]").astype(np.int64), 1)
    n_inv = np.maximum(1, (dur_s + period_s - 1) // period_s)

    total = int(n_inv.sum())
    group_start = np.cumsum(n_inv) - n_inv
    k = np.arange(total) - np.repeat(group_start, n_inv)

    e_started = np.repeat(started, n_inv)
    e_period_s = np.repeat(period_s, n_inv)
    period_start_ts = e_started + (k * e_period_s).astype("timedelta64[s]")
    period_end_ts = period_start_ts + e_period_s.astype("timedelta64[s]")

    invoices = pd.DataFrame(
        {
            "invoice_id": np.arange(1, total + 1, dtype=np.int64),
            "subscription_id": np.repeat(sub_arr, n_inv),
            "user_id": np.repeat(user_arr, n_inv),
            "amount": np.round(np.repeat(price_arr, n_inv), 2),
            "currency": np.repeat(currency_arr, n_inv),
            "period_start": pd.DatetimeIndex(period_start_ts).date,
            "period_end": pd.DatetimeIndex(period_end_ts).date,
            "status": "open",  # provisional; finalized after payments
            "issued_at": period_start_ts,
            "_loaded_at": pd.NaT,
        }
    )
    return invoices, np.arange(total)


def _build_payments(
    gen: np.random.Generator, invoices: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray]:
    total = len(invoices)
    issued = invoices["issued_at"].to_numpy().astype("datetime64[s]")

    pos_batches: list[np.ndarray] = []
    status_batches: list[np.ndarray] = []
    code_batches: list[np.ndarray] = []
    created_batches: list[np.ndarray] = []
    method_batches: list[np.ndarray] = []

    def emit(pos, created, succ):
        method = sample_labels(gen, _METHODS, _METHOD_WEIGHTS, len(pos))
        codes = sample_labels(gen, _FAILURE_CODES, [1.0] * len(_FAILURE_CODES), len(pos))
        pos_batches.append(pos)
        created_batches.append(created)
        method_batches.append(method)
        status_batches.append(np.where(succ, "succeeded", "failed"))
        code_batches.append(np.where(succ, None, codes))

    # --- First attempt for every invoice (vectorized) ---------------------- #
    delay = np.round(lognormal_around(gen, _FIRST_DELAY_MEAN_S, _FIRST_DELAY_SIGMA, total))
    created1 = issued + delay.astype("int64").astype("timedelta64[s]")
    succ1 = gen.random(total) < _P_FIRST_SUCCESS
    emit(np.arange(total), created1, succ1)

    # --- Dunning: 1-2 retries on the failed subset ------------------------- #
    failed_pos = np.flatnonzero(~succ1)
    if failed_pos.size:
        max_retries = gen.integers(1, _MAX_RETRIES + 1, size=failed_pos.size)
        cur_created = created1[~succ1].copy()
        open_ = np.ones(failed_pos.size, dtype=bool)
        for r in range(_MAX_RETRIES):
            ai = np.flatnonzero(open_ & (max_retries > r))
            if ai.size == 0:
                break
            rdelay = np.round(
                lognormal_around(gen, _RETRY_DELAY_MEAN_S, _RETRY_DELAY_SIGMA, ai.size)
            )
            new_created = cur_created[ai] + rdelay.astype("int64").astype("timedelta64[s]")
            succ = gen.random(ai.size) < _P_RETRY_SUCCESS
            emit(failed_pos[ai], new_created, succ)
            cur_created[ai] = new_created
            open_[ai] = open_[ai] & ~succ

    pos = np.concatenate(pos_batches)
    status = np.concatenate(status_batches)
    code = np.concatenate(code_batches)
    created = np.concatenate(created_batches)
    method = np.concatenate(method_batches)

    # Order payment_ids by event time (stable).
    order = np.argsort(created, kind="stable")
    pos, status, code, created, method = (
        pos[order],
        status[order],
        code[order],
        created[order],
        method[order],
    )

    inv_id = invoices["invoice_id"].to_numpy()
    payments = pd.DataFrame(
        {
            "payment_id": np.arange(1, len(pos) + 1, dtype=np.int64),
            "invoice_id": inv_id[pos],
            "user_id": invoices["user_id"].to_numpy()[pos],
            "amount": invoices["amount"].to_numpy()[pos],
            "currency": invoices["currency"].to_numpy()[pos],
            "payment_method": method,
            "status": status,
            "failure_code": code,
            "created_at": created,
            "_loaded_at": pd.NaT,
        }
    )

    inv_paid = np.zeros(total, dtype=bool)
    inv_paid[pos[status == "succeeded"]] = True
    return payments, inv_paid


def _empty_invoices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "invoice_id": pd.array([], dtype="int64"),
            "subscription_id": pd.array([], dtype="int64"),
            "user_id": pd.array([], dtype="int64"),
            "amount": pd.array([], dtype="float64"),
            "currency": pd.array([], dtype="object"),
            "period_start": pd.array([], dtype="object"),
            "period_end": pd.array([], dtype="object"),
            "status": pd.array([], dtype="object"),
            "issued_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )


def _empty_payments() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "payment_id": pd.array([], dtype="int64"),
            "invoice_id": pd.array([], dtype="int64"),
            "user_id": pd.array([], dtype="int64"),
            "amount": pd.array([], dtype="float64"),
            "currency": pd.array([], dtype="object"),
            "payment_method": pd.array([], dtype="object"),
            "status": pd.array([], dtype="object"),
            "failure_code": pd.array([], dtype="object"),
            "created_at": pd.array([], dtype="datetime64[s]"),
            "_loaded_at": pd.array([], dtype="datetime64[s]"),
        }
    )
