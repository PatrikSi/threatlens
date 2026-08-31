from __future__ import annotations

MAX_EVENT_SCHEMA_VERSION_BY_TYPE = {
    "rss_item_new": 2,
    "alert_match": 3,
    "feed_failing": 2,
    "webhook_failed": 1,
    "daily_digest": 1,
    "report_ready": 2,
}


def max_supported_integration_event_schema(event_type: str) -> int:
    return MAX_EVENT_SCHEMA_VERSION_BY_TYPE.get(event_type, 1)
