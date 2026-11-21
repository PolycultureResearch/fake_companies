"""CPG wholesale driver -> affected-MetricFlow-metrics map (ground-truth honesty).

The defining entry: ``spend.<channel>`` affects marketing_spend and NOTHING
else — brand marketing is causally decoupled from sales in this vertical, so
an RCA tool must not attribute a revenue move to an ad-spend change.
"""

from __future__ import annotations

from ..base import RootLabel, RootRule, resolve_root

_SALES_METRICS = [
    "pos_units",
    "pos_dollars",
    "shipped_cases",
    "wholesale_gross_revenue",
    "wholesale_net_revenue",
]

_DRIVER_METRICS: list[tuple[str, list[str]]] = [
    ("spend.", ["marketing_spend"]),  # decoupled: no downstream sales impact
    ("consumer_demand.", _SALES_METRICS),
    ("velocity.", _SALES_METRICS),
]


# Driver -> root: the first metric a rate anomaly on the driver moves, the
# dimension its own member is a value of, and the segment dims the entity
# builder honors (consumer demand reads a per-banner segment).
_ROOTS: list[RootRule] = [
    ("spend.", "marketing_spend", "channel", {}),
    ("consumer_demand.", "pos_units", "category", {"banner": "retailer_banner"}),
    ("velocity.", "pos_units", "retailer_banner", {}),
]


# Metrics that are levels, not flows: over a week or a month take the last
# day, do not add the days up. (The semantic YAML cannot express this.)
STOCK_METRICS: frozenset[str] = frozenset({"trade_inventory_build"})


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["wholesale_net_revenue"]


def root_label_for_driver(driver: str, segment: dict[str, str] | None = None) -> RootLabel:
    return resolve_root(_ROOTS, driver, segment)
