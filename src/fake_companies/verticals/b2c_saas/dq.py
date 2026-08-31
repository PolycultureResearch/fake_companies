"""B2C SaaS data-quality targets: which raw columns a dq event can corrupt."""

from __future__ import annotations

from ..base import DQTableMeta

DQ_TABLE_META: dict[str, DQTableMeta] = {
    "web.sessions": {
        "categorical": ["channel", "country", "device"],
        "nullable": ["user_id", "utm_campaign"],
        "numeric": ["duration_seconds", "page_views"],
    },
    "billing.payments": {
        "categorical": ["currency", "payment_method", "status"],
        "nullable": ["failure_code"],
        "numeric": ["amount"],
    },
    "product.events": {
        "categorical": ["event_name", "plan_at_event", "country", "device"],
        "nullable": [],
        "numeric": [],
    },
    "billing.invoices": {
        "categorical": ["currency", "status"],
        "nullable": [],
        "numeric": ["amount"],
    },
}

# Tables suitable as surprise-dq targets (Tremor dataflow "stars").
DQ_SURPRISE_TABLES = ["product.events", "billing.payments", "web.sessions"]
