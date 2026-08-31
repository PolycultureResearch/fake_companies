"""Retail DTC driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/retail_dtc/models/semantic/metrics.yml``. A rate
anomaly's GroundTruthRecord carries these so detector scoring knows what
should move.
"""

from __future__ import annotations

_DRIVER_METRICS: list[tuple[str, list[str]]] = [
    (
        "spend.",
        [
            "marketing_spend",
            "sessions",
            "orders",
            "new_customers",
            "units",
            "gross_revenue",
            "net_revenue",
        ],
    ),
    ("sessions.", ["sessions", "orders", "new_customers", "units", "gross_revenue", "net_revenue"]),
    (
        "conversion_rate.",
        ["conversion_rate", "orders", "new_customers", "units", "gross_revenue", "net_revenue"],
    ),
    ("items_per_order", ["units", "aov", "gross_revenue", "net_revenue", "gross_margin"]),
    (
        "repeat_rate",
        [
            "returning_customer_orders",
            "repeat_order_share",
            "orders",
            "gross_revenue",
            "net_revenue",
        ],
    ),
    (
        "category_demand.",
        ["units", "gross_revenue", "net_revenue", "gross_margin", "aov"],
    ),
    ("return_rate.", ["return_rate", "refunds", "net_revenue"]),
]


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["net_revenue"]  # every retail driver ultimately feeds net revenue
