"""B2C SaaS driver -> affected-MetricFlow-metrics map (ground-truth honesty).

Names must match ``dbt/b2c_saas/models/semantic/metrics.yml``. A rate anomaly's
GroundTruthRecord carries these so detector scoring knows what should move.
"""

from __future__ import annotations

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


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["mrr"]  # every SaaS driver ultimately feeds MRR
