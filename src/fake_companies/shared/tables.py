"""Table specs for the shared acquisition layer (ad spend + web sessions).

Paired with the shared entity builders in this package; any vertical with a
web/marketing layer includes these specs in its ``RAW_TABLES``.
"""

from __future__ import annotations

from ..output.schemas import TableSpec

AD_SPEND = TableSpec(
    schema="ad_platform",
    name="ad_spend",
    pk="spend_id",
    loaded_at="_loaded_at",
    loading_ref="date",  # DATE -> midnight
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
        "user_id": "BIGINT",  # nullable; the converted app-user / shop-customer id
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
