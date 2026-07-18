from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.integration import IntegrationEvent
from app.services.notification_webhook_templates import DailyDigestContext


DAILY_BRIEF_EVENT_TYPE = "daily_digest"
DAILY_BRIEF_EVENT_SOURCE_TYPE = "ai_daily_brief"
DAILY_BRIEF_EVENT_SCHEMA_VERSION = 1


class DailyBriefNotificationContextError(ValueError):
    pass


def emit_daily_brief_ready_event(db: Session, *, brief: AIDailyBrief) -> IntegrationEvent:
    from app.services.integration_events import emit_integration_event

    if brief.status != "ready":
        raise DailyBriefNotificationContextError("Only a ready AI Daily Brief can emit a notification event")

    payload = build_daily_brief_event_payload(db, brief=brief)
    return emit_integration_event(
        db,
        event_type=DAILY_BRIEF_EVENT_TYPE,
        source_type=DAILY_BRIEF_EVENT_SOURCE_TYPE,
        source_id=brief.id,
        idempotency_key=f"ai_daily_brief:{brief.id}:ready:v{DAILY_BRIEF_EVENT_SCHEMA_VERSION}",
        payload=payload,
    )


def build_daily_brief_event_payload(db: Session, *, brief: AIDailyBrief) -> dict:
    if brief.status != "ready":
        raise DailyBriefNotificationContextError("AI Daily Brief notification context is not ready")
    db.flush()
    if brief.id is None:
        raise DailyBriefNotificationContextError("AI Daily Brief notification context is missing its identifier")

    source_rows = list(
        db.scalars(
            select(AIDailyBriefSourceItem)
            .where(
                AIDailyBriefSourceItem.daily_brief_id == brief.id,
                AIDailyBriefSourceItem.included.is_(True),
            )
            .order_by(AIDailyBriefSourceItem.rank.asc(), AIDailyBriefSourceItem.id.asc())
        )
    )
    feed_names = _unique_nonempty(row.feed_name_snapshot for row in source_rows)
    top_titles = _unique_nonempty(row.title_snapshot for row in source_rows)
    generated_at = brief.generated_at or brief.window_end or datetime.now(timezone.utc)
    scope_key = f"ai_daily_brief:{brief.brief_date.isoformat()}"
    return {
        "daily_brief_id": str(brief.id),
        "brief_date": brief.brief_date.isoformat(),
        "scope_key": scope_key,
        "daily_brief": {
            "schema_version": DAILY_BRIEF_EVENT_SCHEMA_VERSION,
            "id": str(brief.id),
            "date": brief.brief_date.isoformat(),
            "generated_at": _isoformat(generated_at),
            "window_start": _isoformat(brief.window_start),
            "window_end": _isoformat(brief.window_end),
            "title": brief.title or "AI Daily Brief",
            "text": brief.brief_text or "",
            "key_points": _string_list(brief.key_points_json),
            "recommended_actions": _string_list(brief.recommended_actions_json),
            "item_count": max(0, int(brief.item_count or 0)),
            "feed_names": feed_names,
            "top_titles": top_titles,
        },
    }


def daily_brief_context_from_payload(payload: object) -> DailyDigestContext:
    if not isinstance(payload, dict):
        raise DailyBriefNotificationContextError("AI Daily Brief event payload must be an object")
    snapshot = payload.get("daily_brief")
    if not isinstance(snapshot, dict):
        raise DailyBriefNotificationContextError(
            "Legacy rolling daily digest events cannot be delivered as AI Daily Brief notifications"
        )

    brief_id = _required_uuid(snapshot.get("id"), label="daily_brief.id")
    brief_date = _required_date_text(snapshot.get("date"), label="daily_brief.date")
    window_start = _required_datetime(snapshot.get("window_start"), label="daily_brief.window_start")
    window_end = _required_datetime(snapshot.get("window_end"), label="daily_brief.window_end")
    generated_at = _required_datetime(snapshot.get("generated_at"), label="daily_brief.generated_at")
    if window_start > window_end:
        raise DailyBriefNotificationContextError(
            "AI Daily Brief event has daily_brief.window_start after daily_brief.window_end"
        )
    item_count = _nonnegative_int(snapshot.get("item_count"), label="daily_brief.item_count")
    feed_names = _string_list(snapshot.get("feed_names"))
    top_titles = _string_list(snapshot.get("top_titles"))
    return DailyDigestContext(
        window_start=window_start,
        window_end=window_end,
        total_items=item_count,
        total_feeds=len(feed_names),
        feed_names=feed_names,
        top_titles=top_titles,
        brief_id=brief_id,
        brief_date=brief_date,
        generated_at=generated_at,
        title=_required_text(snapshot.get("title"), label="daily_brief.title"),
        brief_text=str(snapshot.get("text") or "").strip(),
        key_points=_string_list(snapshot.get("key_points")),
        recommended_actions=_string_list(snapshot.get("recommended_actions")),
    )


def get_latest_daily_brief_notification_context(db: Session) -> DailyDigestContext | None:
    brief = db.scalar(
        select(AIDailyBrief)
        .where(AIDailyBrief.status == "ready")
        .order_by(AIDailyBrief.brief_date.desc(), AIDailyBrief.generated_at.desc().nullslast())
        .limit(1)
    )
    if brief is None:
        return None
    return daily_brief_context_from_payload(build_daily_brief_event_payload(db, brief=brief))


def _required_uuid(value: object, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event has invalid {label}") from exc


def _required_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyBriefNotificationContextError(f"AI Daily Brief event is missing {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event has invalid {label}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _required_text(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event is missing {label}")
    return text


def _required_date_text(value: object, *, label: str) -> str:
    text = _required_text(value, label=label)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event has invalid {label}") from exc


def _nonnegative_int(value: object, *, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event has invalid {label}") from exc
    if parsed < 0:
        raise DailyBriefNotificationContextError(f"AI Daily Brief event has invalid {label}")
    return parsed


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [text for entry in value if isinstance(entry, str) and (text := entry.strip())]


def _unique_nonempty(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
