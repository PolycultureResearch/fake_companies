"""B2B services driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/b2b_services/models/semantic/metrics.yml``. Stage-gate
entries use the canonical stage names (lead/qualified/proposal/negotiation);
a scenario inventing other stage names falls back to the bookings tail.
"""

from __future__ import annotations

from ..base import RootLabel, RootRule, resolve_root

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


# Driver -> root: the first metric a rate anomaly on the driver moves, the
# dimension its own member is a value of, and the segment dims the entity
# builder honors (only the stage gates read a segment). A stage gate roots at
# the count of deals reaching the *next* stage; a scenario with other stage
# names falls back to deals_won, the end of the funnel.
_STAGE_SEGMENT = {"source": "source", "industry": "industry", "size_tier": "size_tier"}
_ROOTS: list[RootRule] = [
    ("leads.", "leads", "source", {}),
    ("stage_conversion.lead", "qualified_deals", None, _STAGE_SEGMENT),
    ("stage_conversion.qualified", "proposals_sent", None, _STAGE_SEGMENT),
    ("stage_conversion.proposal", "negotiations", None, _STAGE_SEGMENT),
    ("stage_conversion.negotiation", "deals_won", None, _STAGE_SEGMENT),
    ("stage_conversion.", "deals_won", None, _STAGE_SEGMENT),
    ("deal_size", "avg_deal_size", None, {}),
    ("cycle_time_scale", "sales_cycle_days", None, {}),
]


# Metrics that are levels, not flows: over a week or a month take the last
# day, do not add the days up. (The semantic YAML cannot express this.)
STOCK_METRICS: frozenset[str] = frozenset({"open_deals", "open_pipeline_value"})


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["bookings"]  # every B2B driver ultimately feeds bookings


def root_label_for_driver(driver: str, segment: dict[str, str] | None = None) -> RootLabel:
    return resolve_root(_ROOTS, driver, segment)
