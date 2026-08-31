"""Retail DTC data-quality targets: which raw columns a dq event can corrupt."""

from __future__ import annotations

from ..base import DQTableMeta

DQ_TABLE_META: dict[str, DQTableMeta] = {
    "shop_db.orders": {
        "categorical": ["channel", "country", "device"],
        "nullable": ["session_id", "discount_code"],
        "numeric": ["total_amount", "item_count"],
    },
    "shop_db.order_items": {
        "categorical": ["category"],
        "nullable": [],
        "numeric": ["quantity", "line_amount"],
    },
    "web.sessions": {
        "categorical": ["channel", "country", "device"],
        "nullable": ["user_id", "utm_campaign"],
        "numeric": ["duration_seconds", "page_views"],
    },
    "payments.transactions": {
        "categorical": ["payment_method", "status", "kind"],
        "nullable": ["failure_code"],
        "numeric": ["amount"],
    },
    "fulfillment.shipments": {
        "categorical": ["carrier", "status"],
        "nullable": ["shipped_at", "delivered_at"],
        "numeric": [],
    },
    "shop_db.returns": {
        "categorical": ["reason", "category"],
        "nullable": ["refunded_at"],
        "numeric": ["refund_amount"],
    },
}

# Tables suitable as surprise-dq targets (Tremor dataflow "stars").
DQ_SURPRISE_TABLES = [
    "shop_db.orders",
    "shop_db.order_items",
    "web.sessions",
    "payments.transactions",
]
