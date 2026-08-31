"""B2B services data-quality targets: which raw columns a dq event can corrupt."""

from __future__ import annotations

from ..base import DQTableMeta

DQ_TABLE_META: dict[str, DQTableMeta] = {
    "crm.deals": {
        "categorical": ["stage", "source", "industry", "size_tier"],
        "nullable": ["closed_at"],
        "numeric": ["amount"],
    },
    "crm.deal_stage_events": {
        "categorical": ["stage"],
        "nullable": [],
        "numeric": [],
    },
    "billing.invoices": {
        "categorical": ["kind", "status"],
        "nullable": [],
        "numeric": ["amount"],
    },
    "billing.payments": {
        "categorical": ["method"],
        "nullable": [],
        "numeric": ["amount"],
    },
}

# Tables suitable as surprise-dq targets (Tremor dataflow "stars").
DQ_SURPRISE_TABLES = ["crm.deals", "crm.deal_stage_events", "billing.invoices"]
