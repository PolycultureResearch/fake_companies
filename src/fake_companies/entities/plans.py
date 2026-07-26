"""Plan catalog: the ``app_db.plans`` frame + a shared plan index.

The :class:`PlanIndex` is the contract lifecycle/billing/usage share for mapping
between (plan name, billing period) and the surrogate ``plan_id`` used across the
subscription and billing tables.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from ..config import ScenarioConfig


@dataclass(frozen=True)
class PlanRow:
    plan_id: int
    name: str
    billing_period: str  # monthly | annual
    price: float


class PlanIndex:
    def __init__(self, rows: list[PlanRow], currency: str):
        self.rows = rows
        self.currency = currency
        self._by_id = {r.plan_id: r for r in rows}
        self._by_key = {(r.name, r.billing_period): r for r in rows}

    def id_for(self, name: str, period: str) -> int:
        """Plan id for (name, period), falling back to any period for that name."""
        row = self._by_key.get((name, period))
        if row is not None:
            return row.plan_id
        for r in self.rows:
            if r.name == name:
                return r.plan_id
        raise KeyError((name, period))

    def has_period(self, name: str, period: str) -> bool:
        return (name, period) in self._by_key

    def row(self, plan_id: int) -> PlanRow:
        return self._by_id[plan_id]

    def monthly_price(self, plan_id: int) -> float:
        """Normalize any plan to its monthly-equivalent price (MRR contribution)."""
        r = self._by_id[plan_id]
        return r.price / 12.0 if r.billing_period == "annual" else r.price

    def _name_prices(self) -> dict[str, float]:
        """Canonical monthly-equivalent price per plan name (prefers the monthly row)."""
        out: dict[str, float] = {}
        for r in self.rows:
            mp = r.price if r.billing_period == "monthly" else r.price / 12.0
            if r.name not in out or r.billing_period == "monthly":
                out[r.name] = mp
        return out

    @property
    def paid_ladder(self) -> list[str]:
        """Paid plan names ordered by monthly price ascending (the upgrade ladder)."""
        prices = self._name_prices()
        return sorted((n for n, p in prices.items() if p > 0), key=lambda n: prices[n])

    @property
    def free_name(self) -> str | None:
        """Name of the free/zero-price baseline tier, if any."""
        return next((n for n, p in self._name_prices().items() if p == 0), None)

    def frame(self, loaded_at: dt.datetime) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "plan_id": [r.plan_id for r in self.rows],
                "name": [r.name for r in self.rows],
                "billing_period": [r.billing_period for r in self.rows],
                "price": [r.price for r in self.rows],
                "currency": [self.currency] * len(self.rows),
                "_loaded_at": [loaded_at] * len(self.rows),
            }
        )


def build_plan_index(cfg: ScenarioConfig) -> PlanIndex:
    rows: list[PlanRow] = []
    pid = 1
    for plan in cfg.plans.plans:
        # Every plan has a monthly row; paid plans also get an annual row.
        rows.append(PlanRow(pid, plan.name, "monthly", float(plan.monthly_price)))
        pid += 1
        # A plan gets an annual row iff it has a non-zero annual price (free = 0).
        if plan.annual_price:
            rows.append(PlanRow(pid, plan.name, "annual", float(plan.annual_price)))
            pid += 1
    return PlanIndex(rows, cfg.company.currency)
