"""Retail DTC driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/retail_dtc/models/semantic/metrics.yml``. A rate
anomaly's GroundTruthRecord carries these so detector scoring knows what
should move.
"""

from __future__ import annotations

from ..base import RootLabel, RootRule, resolve_root

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


# Driver -> root: the first metric a rate anomaly on the driver moves, the
# dimension its own member is a value of, and the segment dims the entity
# builder honors (only session conversion reads a segment; a segment on any
# other driver is a no-op, so it adds nothing to the slice).
#
# The root is the closest metric MetricFlow can slice by that dimension:
# ``return_rate.<category>`` roots at ``returned_units``, not ``return_rate``,
# because the ratio's two sides sit on different semantic models and no single
# group-by addresses its category. ``category_demand.<category>`` roots at
# ``units`` *by category*: it reweights the basket mix, so total units barely
# move and the anomaly is visible only in the slice.
_ROOTS: list[RootRule] = [
    ("spend.", "marketing_spend", "channel", {}),
    ("sessions.", "sessions", "channel", {}),
    ("conversion_rate.", "conversion_rate", "channel", {"country": "country", "device": "device"}),
    ("items_per_order", "units", None, {}),
    ("repeat_rate", "returning_customer_orders", None, {}),
    ("category_demand.", "units", "category", {}),
    ("return_rate.", "returned_units", "category", {}),
]


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["net_revenue"]  # every retail driver ultimately feeds net revenue


def root_label_for_driver(driver: str, segment: dict[str, str] | None = None) -> RootLabel:
    return resolve_root(_ROOTS, driver, segment)
