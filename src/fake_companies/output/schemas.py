"""Central registry of raw source-table schemas.

This is the contract shared by the entity generators (which produce the frames),
the DuckDB writer (which enforces column order/types), and the dbt staging models
(which read them). Every raw table carries a ``_loaded_at`` column; tables Tremor
profiles in dataflow mode additionally declare an ``event_time`` column.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableSpec:
    schema: str
    name: str
    columns: dict[str, str]  # column name -> DuckDB type, in canonical order
    pk: str | None = None
    event_time: str | None = None  # business event time (Tremor dataflow bucketing)
    loaded_at: str = "_loaded_at"
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    def ddl(self) -> str:
        cols = ",\n  ".join(f'"{c}" {t}' for c, t in self.columns.items())
        return f"CREATE OR REPLACE TABLE {self.fqn} (\n  {cols}\n)"


# --------------------------------------------------------------------------- #
# Raw source tables
# --------------------------------------------------------------------------- #
AD_SPEND = TableSpec(
    schema="ad_platform",
    name="ad_spend",
    pk="spend_id",
    loaded_at="_loaded_at",
    columns={
        "spend_id": "BIGINT",
        "date": "DATE",
        "channel": "VARCHAR",
        "campaign_id": "VARCHAR",
        "impressions": "BIGINT",
        "clicks": "BIGINT",
        "spend": "DOUBLE",
        "currency": "VARCHAR",
        "_loaded_at": "TIMESTAMP",
    },
)

SESSIONS = TableSpec(
    schema="web",
    name="sessions",
    pk="session_id",
    event_time="started_at",
    columns={
        "session_id": "BIGINT",
        "anonymous_id": "VARCHAR",
        "user_id": "BIGINT",  # nullable
        "channel": "VARCHAR",
        "utm_campaign": "VARCHAR",  # nullable
        "country": "VARCHAR",
        "device": "VARCHAR",
        "started_at": "TIMESTAMP",
        "duration_seconds": "INTEGER",
        "page_views": "INTEGER",
        "landing_page": "VARCHAR",
        "_loaded_at": "TIMESTAMP",
    },
)

USERS = TableSpec(
    schema="app_db",
    name="users",
    pk="user_id",
    columns={
        "user_id": "BIGINT",
        "email": "VARCHAR",
        "full_name": "VARCHAR",
        "country": "VARCHAR",
        "signup_channel": "VARCHAR",
        "device_at_signup": "VARCHAR",
        "created_at": "TIMESTAMP",
        "_loaded_at": "TIMESTAMP",
    },
)

PLANS = TableSpec(
    schema="app_db",
    name="plans",
    pk="plan_id",
    columns={
        "plan_id": "BIGINT",
        "name": "VARCHAR",
        "billing_period": "VARCHAR",
        "price": "DOUBLE",
        "currency": "VARCHAR",
        "_loaded_at": "TIMESTAMP",
    },
)

SUBSCRIPTIONS = TableSpec(
    schema="app_db",
    name="subscriptions",
    pk="subscription_id",
    columns={
        "subscription_id": "BIGINT",
        "user_id": "BIGINT",
        "plan_id": "BIGINT",
        "status": "VARCHAR",  # trialing | active | past_due | canceled
        "trial_start_at": "TIMESTAMP",
        "trial_end_at": "TIMESTAMP",
        "started_at": "TIMESTAMP",  # paid start (nullable)
        "canceled_at": "TIMESTAMP",  # nullable
        "_loaded_at": "TIMESTAMP",
    },
)

SUBSCRIPTION_EVENTS = TableSpec(
    schema="app_db",
    name="subscription_events",
    pk="event_id",
    event_time="occurred_at",
    columns={
        "event_id": "BIGINT",
        "subscription_id": "BIGINT",
        "user_id": "BIGINT",
        "event_type": "VARCHAR",
        "from_plan_id": "BIGINT",  # nullable
        "to_plan_id": "BIGINT",  # nullable
        "occurred_at": "TIMESTAMP",
        "_loaded_at": "TIMESTAMP",
    },
)

INVOICES = TableSpec(
    schema="billing",
    name="invoices",
    pk="invoice_id",
    event_time="issued_at",
    columns={
        "invoice_id": "BIGINT",
        "subscription_id": "BIGINT",
        "user_id": "BIGINT",
        "amount": "DOUBLE",
        "currency": "VARCHAR",
        "period_start": "DATE",
        "period_end": "DATE",
        "status": "VARCHAR",  # paid | open | void | uncollectible
        "issued_at": "TIMESTAMP",
        "_loaded_at": "TIMESTAMP",
    },
)

PAYMENTS = TableSpec(
    schema="billing",
    name="payments",
    pk="payment_id",
    event_time="created_at",
    columns={
        "payment_id": "BIGINT",
        "invoice_id": "BIGINT",
        "user_id": "BIGINT",
        "amount": "DOUBLE",
        "currency": "VARCHAR",
        "payment_method": "VARCHAR",
        "status": "VARCHAR",  # succeeded | failed
        "failure_code": "VARCHAR",  # nullable
        "created_at": "TIMESTAMP",
        "_loaded_at": "TIMESTAMP",
    },
)

PRODUCT_EVENTS = TableSpec(
    schema="product",
    name="events",
    pk="event_id",
    event_time="occurred_at",
    columns={
        "event_id": "BIGINT",
        "user_id": "BIGINT",
        "event_name": "VARCHAR",
        "plan_at_event": "VARCHAR",
        "country": "VARCHAR",
        "device": "VARCHAR",
        "occurred_at": "TIMESTAMP",
        "_loaded_at": "TIMESTAMP",
    },
)

# --------------------------------------------------------------------------- #
# Meta tables
# --------------------------------------------------------------------------- #
GROUND_TRUTH = TableSpec(
    schema="meta",
    name="ground_truth",
    pk="id",
    loaded_at="",
    columns={
        "id": "VARCHAR",
        "kind": "VARCHAR",
        "type": "VARCHAR",
        "origin": "VARCHAR",
        "target": "VARCHAR",
        "segment": "VARCHAR",
        "start_date": "DATE",
        "end_date": "DATE",
        "magnitude": "DOUBLE",
        "affected_metrics": "VARCHAR",
        "affected_signals": "VARCHAR",
        "params": "VARCHAR",
    },
)

RUN_MANIFEST = TableSpec(
    schema="meta",
    name="run_manifest",
    loaded_at="",
    columns={
        "key": "VARCHAR",
        "value": "VARCHAR",
    },
)

RAW_TABLES: list[TableSpec] = [
    AD_SPEND,
    SESSIONS,
    USERS,
    PLANS,
    SUBSCRIPTIONS,
    SUBSCRIPTION_EVENTS,
    INVOICES,
    PAYMENTS,
    PRODUCT_EVENTS,
]

META_TABLES: list[TableSpec] = [GROUND_TRUTH, RUN_MANIFEST]

ALL_TABLES: list[TableSpec] = RAW_TABLES + META_TABLES

BY_FQN: dict[str, TableSpec] = {t.fqn: t for t in ALL_TABLES}
