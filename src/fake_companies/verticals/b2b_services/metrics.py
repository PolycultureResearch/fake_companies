"""B2B services driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/b2b_services/models/semantic/metrics.yml``. Stage-gate
entries use the canonical stage names (lead/qualified/proposal/negotiation);
a scenario inventing other stage names falls back to the bookings tail.
"""

from __future__ import annotations

_DRIVER_METRICS: list[tuple[str, list[str]]] = [
    (
        "leads.",
        [
            "leads",
            "qualified_deals",
            "proposals_sent",
            "negotiations",
            "deals_won",
            "bookings",
            "invoiced_revenue",
            "open_deals",
            "open_pipeline_value",
        ],
    ),
    (
        "stage_conversion.lead",
        [
            "qualified_deals",
            "proposals_sent",
            "negotiations",
            "deals_won",
            "win_rate",
            "bookings",
            "invoiced_revenue",
        ],
    ),
    (
        "stage_conversion.qualified",
        ["proposals_sent", "negotiations", "deals_won", "win_rate", "bookings", "invoiced_revenue"],
    ),
    (
        "stage_conversion.proposal",
        ["negotiations", "deals_won", "win_rate", "bookings", "invoiced_revenue"],
    ),
    (
        "stage_conversion.negotiation",
        ["deals_won", "win_rate", "bookings", "invoiced_revenue"],
    ),
    ("deal_size", ["avg_deal_size", "bookings", "invoiced_revenue", "open_pipeline_value"]),
    ("cycle_time_scale", ["sales_cycle_days", "open_deals", "open_pipeline_value"]),
]


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["bookings"]  # every B2B driver ultimately feeds bookings
