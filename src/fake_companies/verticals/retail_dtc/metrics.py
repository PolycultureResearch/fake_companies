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
            "customer_base",
        ],
    ),
    (
        "sessions.",
        [
            "sessions",
            "orders",
            "new_customers",
            "units",
            "gross_revenue",
            "net_revenue",
            "customer_base",
        ],
    ),
    (
        "conversion_rate.",
        [
            "conversion_rate",
            "orders",
            "new_customers",
            "units",
            "gross_revenue",
            "net_revenue",
            "customer_base",
        ],
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
    # A mix shift: it reweights which categories land in baskets and nothing
    # else, so total units and orders cannot move (measured: exactly zero).
    # What moves in total is what depends on the price and margin of the mix;
    # a slump in a cheap category *raises* revenue and AOV. Units move only by
    # category, which is this driver's root slice (see _ROOTS).
    (
        "category_demand.",
        [
            "avg_unit_price",
            "item_revenue",
            "gross_revenue",
            "net_revenue",
            "gross_margin",
            "aov",
            "margin_rate",
        ],
    ),
    ("return_rate.", ["return_rate", "refunds", "net_revenue"]),
    # A list-price change: the category's own price per unit moves (`within`),
    # and its share of basket lines falls by the price elasticity (`mix`), so
    # total units still do not move. Revenue, AOV and margin follow the price.
    (
        "price.",
        [
            "avg_unit_price",
            "item_revenue",
            "gross_revenue",
            "net_revenue",
            "gross_margin",
            "aov",
            "margin_rate",
        ],
    ),
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
# ``units`` *by category*: it reweights the basket mix, so total units do not
# move and the anomaly is visible only in the slice; the total it moves is
# ``avg_unit_price``, by mix (``_ROOT_MIXES``). ``price.<category>`` roots at
# ``avg_unit_price`` by category: the category's own price per unit changed.
_ROOTS: list[RootRule] = [
    ("spend.", "marketing_spend", "channel", {}),
    ("sessions.", "sessions", "channel", {}),
    ("conversion_rate.", "conversion_rate", "channel", {"country": "country", "device": "device"}),
    ("items_per_order", "units", None, {}),
    ("repeat_rate", "returning_customer_orders", None, {}),
    ("category_demand.", "units", "category", {}),
    ("return_rate.", "returned_units", "category", {}),
    ("price.", "avg_unit_price", "category", {}),
]


# Metrics that are levels, not flows: over a week or a month take the last
# day, do not add the days up. (The semantic YAML cannot express this.)
STOCK_METRICS: frozenset[str] = frozenset({"customer_base"})


# customer_base is the running total of new_customers, so every driver that
# moves new_customers moves it, and from then on. discounted_orders counts the
# orders that took a promo code; codes exist only inside a promotion's window
# (``promos`` in the config), so a driver that moves orders moves it only when
# the anomaly's window overlaps a promotion: see affected_metrics_in_window.
_MOVES_ORDERS = ("spend.", "sessions.", "conversion_rate.", "repeat_rate")


def affected_metrics_in_window(cfg, driver: str, start, end) -> list[str]:
    """The declared metrics of a rate anomaly on ``driver`` over ``start`` ..
    ``end`` (None: open-ended): the driver's own list, plus
    ``discounted_orders`` when the driver moves orders and the window overlaps
    one of the config's promotions."""
    metrics = list(affected_metrics_for_driver(driver))
    moves_orders = any(
        driver.startswith(p) if p.endswith(".") else driver == p for p in _MOVES_ORDERS
    )
    if moves_orders:
        for promo in getattr(cfg, "promos", None) or []:
            p0 = promo.window.start
            p1 = promo.window.end or promo.window.start
            if p0 <= (end or p1) and start <= p1:
                metrics.append("discounted_orders")
                break
    return metrics


def affected_metrics_for_driver(driver: str) -> list[str]:
    for prefix, metrics in _DRIVER_METRICS:
        if prefix.endswith(".") and driver.startswith(prefix):
            return metrics
        if not prefix.endswith(".") and driver == prefix:
            return metrics
    return ["net_revenue"]  # every retail driver ultimately feeds net revenue


# Roots that are a rate's numerator count -> the rate itself (not sliceable by
# one MetricFlow group-by, which is why it is not the root).
_ROOT_RATES = {"returned_units": "return_rate"}

# Drivers whose anomaly is a mix shift -> the ratio whose total it moves (the
# root slice's share of that ratio's denominator is what changed).
_ROOT_MIXES = {"category_demand.": "avg_unit_price"}


def root_label_for_driver(driver: str, segment: dict[str, str] | None = None) -> RootLabel:
    return resolve_root(_ROOTS, driver, segment, _ROOT_RATES, _ROOT_MIXES)
