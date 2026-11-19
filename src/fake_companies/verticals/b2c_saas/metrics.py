"""B2C SaaS driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/b2c_saas/models/semantic/metrics.yml``. A rate anomaly's
GroundTruthRecord carries these so detector scoring knows what should move.
"""

from __future__ import annotations

from ..base import RootLabel, RootRule, resolve_root

_DRIVER_METRICS: list[tuple[str, list[str]]] = [
    (
        "spend.",
        [
            "marketing_spend",
            "sessions",
            "signups",
            "trials_started",
            "new_subscriptions",
            "new_mrr",
            "mrr",
        ],
    ),
    ("sessions.", ["sessions", "signups", "trials_started", "new_subscriptions", "new_mrr", "mrr"]),
    (
        "signup_rate.",
        ["visit_signup_rate", "signups", "trials_started", "new_subscriptions", "new_mrr"],
    ),
    ("trial_start_rate", ["trials_started", "new_subscriptions", "new_mrr"]),
    ("trial_convert", ["trial_conversion_rate", "new_subscriptions", "new_mrr"]),
    (
        "churn.",
        [
            "churned_subscriptions",
            "customer_churn_rate",
            "churned_mrr",
            "active_subscriptions",
            "mrr",
        ],
    ),
    ("upgrade", ["expansion_mrr", "mrr"]),
    ("downgrade", ["contraction_mrr", "mrr"]),
    ("resurrect", ["reactivations", "new_subscriptions", "mrr"]),
    ("direct_convert", ["direct_conversions", "new_subscriptions", "new_mrr", "mrr"]),
    (
        "trial_engagement",
        [
            "trial_activation_rate",
            "trial_days_active",
            "trial_conversion_rate",
            "trial_conversions",
            "new_subscriptions",
            "new_mrr",
            "product_events",
        ],
    ),
    (
        "member_engagement",
        [
            "member_activity_rate",
            "dau",
            "wau",
            "product_events",
            "customer_churn_rate",
            "churned_subscriptions",
            "churned_mrr",
            "active_subscriptions",
            "mrr",
        ],
    ),
    ("dau_over_active.", ["dau", "wau", "product_events"]),
    ("events_per_active_day.", ["product_events", "dau"]),
]


# Driver -> root: the first metric a rate anomaly on the driver moves, the
# dimension its own member is a value of, and the segment dims the entity
# builder honors (only the signup funnel reads a segment).
#
# The root is the closest metric MetricFlow can slice by that dimension:
# ``churn.<plan>`` roots at ``churned_subscriptions``, not
# ``customer_churn_rate``, whose two sides sit on different semantic models.
_ROOTS: list[RootRule] = [
    ("spend.", "marketing_spend", "channel", {}),
    ("sessions.", "sessions", "channel", {}),
    ("signup_rate.", "visit_signup_rate", "channel", {"country": "country", "device": "device"}),
    ("trial_start_rate", "trials_started", None, {}),
    ("trial_convert", "trial_conversion_rate", None, {}),
    ("churn.", "churned_subscriptions", "plan", {}),
    ("upgrade", "expansion_mrr", None, {}),
    ("downgrade", "contraction_mrr", None, {}),
    ("resurrect", "reactivations", None, {}),
    ("direct_convert", "direct_conversions", None, {}),
    ("trial_engagement", "trial_days_active", None, {}),
    ("member_engagement", "member_activity_rate", None, {}),
    ("dau_over_active.", "dau", "plan", {}),
    ("events_per_active_day.", "product_events", "plan", {}),
]


# Metrics that are levels, not flows: over a week or a month take the last
# day, do not add the days up. (The semantic YAML cannot express this.)
STOCK_METRICS: frozenset[str] = frozenset({"mrr", "active_subscriptions"})


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["mrr"]  # every SaaS driver ultimately feeds MRR


def root_label_for_driver(driver: str, segment: dict[str, str] | None = None) -> RootLabel:
    return resolve_root(_ROOTS, driver, segment)
