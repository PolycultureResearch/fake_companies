"""CPG wholesale data-quality targets: which raw columns a dq event can corrupt."""

from __future__ import annotations

from ..base import DQTableMeta

DQ_TABLE_META: dict[str, DQTableMeta] = {
    "pos.scan_sales": {
        "categorical": ["retailer_banner", "channel_type", "category"],
        "nullable": [],
        "numeric": ["units", "dollars"],
    },
    "erp.shipments": {
        "categorical": ["channel_type", "category"],
        "nullable": [],
        "numeric": ["cases", "net_amount"],
    },
}

# Tables suitable as surprise-dq targets (Tremor dataflow "stars").
DQ_SURPRISE_TABLES = ["pos.scan_sales", "erp.shipments"]
