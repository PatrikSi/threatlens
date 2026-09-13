"""Validated presentation snapshots never carry another analyst's private selections."""

import json

from app.schemas.view import SavedViewQueryPayload


def sanitize_dashboard_template(
    value: SavedViewQueryPayload | None,
) -> SavedViewQueryPayload | None:
    if value is None:
        return None
    payload = value.model_dump(mode="json")
    if len(payload["windows"]) > 12:
        raise ValueError("A workspace template can contain at most 12 panels.")
    for filters in (payload["rss_filters"], payload["alert_filters"]):
        _clear_private_selection(filters)
    for index, window in enumerate(payload["windows"]):
        window["id"] = f"organization-panel-{index + 1}"
        window["scratch_note"] = ""
        window["selected_daily_brief_id"] = None
        for field in ("rss_filters", "alert_filters"):
            if window.get(field):
                _clear_private_selection(window[field])
                window[field]["page"] = 1
    if _has_unsafe_text(payload):
        raise ValueError("Workspace templates must contain storage-safe Unicode text.")
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(serialized.encode("utf-8")) > 65_536:
        raise ValueError("The workspace template exceeds the 64 KiB limit.")
    return SavedViewQueryPayload.model_validate(payload)


def _clear_private_selection(filters: dict) -> None:
    for field in ("selected_feed_ids", "selected_alert_ids"):
        if field in filters:
            filters[field] = []


def _has_unsafe_text(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, dict):
        return any(_has_unsafe_text(entry) for entry in value.values())
    if isinstance(value, list):
        return any(_has_unsafe_text(entry) for entry in value)
    return False
