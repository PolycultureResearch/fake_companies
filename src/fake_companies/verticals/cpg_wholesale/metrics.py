"""CPG wholesale driver -> affected-MetricFlow-metrics map (ground-truth honesty).

The defining entry: ``spend.<channel>`` affects marketing_spend and NOTHING
else — brand marketing is causally decoupled from sales in this vertical, so
an RCA tool must not attribute a revenue move to an ad-spend change.
"""

from __future__ import annotations

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


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["wholesale_net_revenue"]
