from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from types import SimpleNamespace

from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.schemas.notification import (
    NotificationEventType,
    NotificationTemplateVariable,
    NotificationWebhookField,
    NotificationWebhookWrite,
)
from app.services.url_utils import normalize_url, redact_feed_url


class TemplateRenderError(ValueError):
    pass


@dataclass(frozen=True)
class AlertMatchContext:
    count: int
    primary_name: str
    names: list[str]
    categories: list[str]
    matched_keywords: list[str]


@dataclass(frozen=True)
class FailedWebhookContext:
    id: uuid.UUID
    name: str
    event_type: str
    status_code: int | None
    error: str | None
    attempted_at: datetime


@dataclass(frozen=True)
class DailyDigestContext:
    window_start: datetime
    window_end: datetime
    total_items: int
    total_feeds: int
    feed_names: list[str]
    top_titles: list[str]
    brief_id: uuid.UUID | None = None
    brief_date: str | None = None
    generated_at: datetime | None = None
    title: str = ""
    brief_text: str = ""
    key_points: list[str] | None = None
    recommended_actions: list[str] | None = None
    brief_url: str = ""


TEMPLATE_VARIABLES: tuple[NotificationTemplateVariable, ...] = (
    NotificationTemplateVariable(key="event.type", description="Notification event type.", example="rss_item_new"),
    NotificationTemplateVariable(
        key="event.triggered_at",
        description="UTC timestamp for this delivery attempt.",
        example="2026-03-25T12:30:00+00:00",
    ),
    NotificationTemplateVariable(
        key="event.delivery_id",
        description="Unique identifier for this webhook delivery.",
        example="8a95c9a9-70bc-4a9f-b1a3-13c9b92f17f4",
    ),
    NotificationTemplateVariable(key="user.id", description="Owner of the webhook.", example="9c24d6bd-23c2-4c4d-9097-fd8c88d9eafd"),
    NotificationTemplateVariable(key="user.email", description="Owner email address.", example="analyst@example.com"),
    NotificationTemplateVariable(key="feed.id", description="Feed identifier.", example="01b5d2a8-4734-4b95-8c7c-51171f260432"),
    NotificationTemplateVariable(key="feed.name", description="Feed display name.", example="Unit42 RSS"),
    NotificationTemplateVariable(
        key="feed.url",
        description="Redacted feed source URL suitable for notifications.",
        example="https://example.com/feed.xml",
    ),
    NotificationTemplateVariable(key="feed.site_url", description="Feed website URL when known.", example="https://example.com"),
    NotificationTemplateVariable(key="feed.error_count", description="Consecutive feed fetch failures.", example="3"),
    NotificationTemplateVariable(key="feed.last_error", description="Latest feed error message.", example="http_status:500"),
    NotificationTemplateVariable(
        key="feed.last_fetch_at",
        description="Last fetch attempt timestamp for the feed.",
        example="2026-03-25T12:29:00+00:00",
    ),
    NotificationTemplateVariable(
        key="feed.last_success_at",
        description="Last successful fetch timestamp for the feed.",
        example="2026-03-24T22:15:00+00:00",
    ),
    NotificationTemplateVariable(key="item.id", description="New item identifier.", example="5e2db70d-0a5b-428e-a9bf-0b4be612cbab"),
    NotificationTemplateVariable(key="item.title", description="Item title.", example="New campaign targeting exposed edge devices"),
    NotificationTemplateVariable(key="item.url", description="Original item URL.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.canonical_url", description="Canonical item URL when known.", example="https://example.com/articles/campaign"),
    NotificationTemplateVariable(key="item.summary", description="Feed summary text.", example="Researchers observed a fresh wave of exploitation."),
    NotificationTemplateVariable(key="item.status", description="ThreatLens item status.", example="new"),
    NotificationTemplateVariable(key="item.published_at", description="Published timestamp when provided by the feed.", example="2026-03-25T09:15:00+00:00"),
    NotificationTemplateVariable(key="item.first_seen_at", description="First time ThreatLens saw the item.", example="2026-03-25T09:16:02+00:00"),
    NotificationTemplateVariable(key="alert.count", description="Number of alert interests matched by the item.", example="2"),
    NotificationTemplateVariable(key="alert.primary_name", description="Name of the first matched alert.", example="Ransomware Watch"),
    NotificationTemplateVariable(key="alert.names", description="Comma-separated matched alert names.", example="Ransomware Watch, Credential Theft"),
    NotificationTemplateVariable(key="alert.categories", description="Comma-separated matched alert categories.", example="malware, identity"),
    NotificationTemplateVariable(
        key="alert.matched_keywords",
        description="Comma-separated keywords that matched across all alerts.",
        example="lockbit, initial access",
    ),
    NotificationTemplateVariable(
        key="failed_webhook.id",
        description="Identifier of the webhook delivery source that failed.",
        example="bf8916bf-b537-4f66-941a-c126b204f826",
    ),
    NotificationTemplateVariable(key="failed_webhook.name", description="Name of the webhook that failed.", example="Slack critical feed"),
    NotificationTemplateVariable(
        key="failed_webhook.event_type",
        description="Event type handled by the webhook that failed.",
        example="rss_item_new",
    ),
    NotificationTemplateVariable(key="failed_webhook.status_code", description="HTTP status code from the failed delivery.", example="500"),
    NotificationTemplateVariable(key="failed_webhook.error", description="Failure message from the failed delivery.", example="HTTP 500"),
    NotificationTemplateVariable(
        key="failed_webhook.attempted_at",
        description="Timestamp of the failed delivery attempt.",
        example="2026-03-25T12:35:00+00:00",
    ),
    NotificationTemplateVariable(
        key="digest.window_start",
        description="Start of the digest coverage window in UTC.",
        example="2026-03-24T12:00:00+00:00",
    ),
    NotificationTemplateVariable(
        key="digest.window_end",
        description="End of the digest coverage window in UTC.",
        example="2026-03-25T12:00:00+00:00",
    ),
    NotificationTemplateVariable(key="digest.total_items", description="Total items included in the digest.", example="18"),
    NotificationTemplateVariable(key="digest.total_feeds", description="Number of feeds represented in the digest.", example="6"),
    NotificationTemplateVariable(
        key="digest.feed_names",
        description="Comma-separated feed names represented in the digest.",
        example="Unit42 RSS, CISA",
    ),
    NotificationTemplateVariable(
        key="digest.top_titles",
        description="Newline-separated sample titles from the digest window.",
        example="Threat report one\nThreat report two",
    ),
    NotificationTemplateVariable(key="brief.id", description="AI Daily Brief identifier.", example="a8636d41-f2e8-4690-a385-272e6f852441"),
    NotificationTemplateVariable(key="brief.date", description="UTC date represented by the AI Daily Brief.", example="2026-03-25"),
    NotificationTemplateVariable(
        key="brief.generated_at",
        description="Timestamp recorded when AI Daily Brief generation ran.",
        example="2026-03-25T09:00:04+00:00",
    ),
    NotificationTemplateVariable(key="brief.window_start", description="Start of the AI Daily Brief source window in UTC.", example="2026-03-24T09:00:00+00:00"),
    NotificationTemplateVariable(key="brief.window_end", description="End of the AI Daily Brief source window in UTC.", example="2026-03-25T09:00:00+00:00"),
    NotificationTemplateVariable(key="brief.title", description="Generated AI Daily Brief title.", example="Daily threat intelligence brief"),
    NotificationTemplateVariable(key="brief.title_html", description="HTML-escaped generated AI Daily Brief title.", example="Daily threat intelligence brief"),
    NotificationTemplateVariable(key="brief.url", description="ThreatLens location for the generated brief or report when available.", example="/reporting/a8636d41-f2e8-4690-a385-272e6f852441"),
    NotificationTemplateVariable(key="brief.url_html", description="HTML-escaped ThreatLens location for the generated brief or report.", example="/reporting/a8636d41-f2e8-4690-a385-272e6f852441"),
    NotificationTemplateVariable(key="brief.text", description="Generated AI Daily Brief narrative.", example="Identity-focused campaigns remain the highest-priority development."),
    NotificationTemplateVariable(key="brief.text_html", description="HTML-escaped AI Daily Brief narrative with preserved line breaks.", example="Identity-focused campaigns remain the highest-priority development."),
    NotificationTemplateVariable(key="brief.item_count", description="Number of source items considered for the AI Daily Brief.", example="18"),
    NotificationTemplateVariable(key="brief.key_points", description="Newline-separated generated key points.", example="Review identity telemetry\nTrack exposed edge devices"),
    NotificationTemplateVariable(key="brief.key_points_html", description="HTML-escaped generated key points separated by line breaks.", example="Review identity telemetry<br>Track exposed edge devices"),
    NotificationTemplateVariable(key="brief.recommended_actions", description="Newline-separated generated recommended actions.", example="Validate MFA coverage\nReview edge patch status"),
    NotificationTemplateVariable(key="brief.recommended_actions_html", description="HTML-escaped generated recommended actions separated by line breaks.", example="Validate MFA coverage<br>Review edge patch status"),
)
TEMPLATE_VARIABLE_KEYS = frozenset(variable.key for variable in TEMPLATE_VARIABLES)
TEMPLATE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def list_template_variables() -> list[NotificationTemplateVariable]:
    return list(TEMPLATE_VARIABLES)


def find_unknown_template_variables_in_texts(fragments: Iterable[str | None]) -> set[str]:
    unknown: set[str] = set()
    for fragment in fragments:
        if not fragment:
            continue
        for match in TEMPLATE_PATTERN.findall(fragment):
            if match not in TEMPLATE_VARIABLE_KEYS:
                unknown.add(match)
    return unknown


def render_notification_template_text(
    template: str,
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    event_type: NotificationEventType,
    triggered_at: datetime | None = None,
    delivery_id: uuid.UUID | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
) -> str:
    context = build_template_context(
        user=user,
        feed=feed,
        item=item,
        event_type=event_type,
        triggered_at=triggered_at or datetime.now(timezone.utc),
        delivery_id=delivery_id or uuid.uuid4(),
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    return render_template(template, context)


def find_unknown_template_variables(payload: NotificationWebhookWrite) -> set[str]:
    template_fragments = [payload.url_template]
    for fields in (payload.query_params, payload.headers, payload.body_fields):
        for field in fields:
            template_fragments.extend([field.key, field.value])
    if payload.body_template:
        template_fragments.append(payload.body_template)
    return find_unknown_template_variables_in_texts(template_fragments)


def build_template_context(
    *,
    user: User | SimpleNamespace,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    event_type: NotificationEventType,
    triggered_at: datetime,
    delivery_id: uuid.UUID,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
) -> dict[str, str]:
    return {
        "event.type": event_type,
        "event.triggered_at": isoformat(triggered_at),
        "event.delivery_id": str(delivery_id),
        "user.id": str(getattr(user, "id", "")),
        "user.email": getattr(user, "email", "") or "",
        "feed.id": str(getattr(feed, "id", "")),
        "feed.name": getattr(feed, "name", "") or "",
        "feed.url": redact_feed_url(getattr(feed, "url", "") or ""),
        "feed.site_url": getattr(feed, "site_url", "") or "",
        "feed.error_count": str(getattr(feed, "error_count", "") or ""),
        "feed.last_error": getattr(feed, "last_error", "") or "",
        "feed.last_fetch_at": isoformat(getattr(feed, "last_fetch_at", None)),
        "feed.last_success_at": isoformat(getattr(feed, "last_success_at", None)),
        "item.id": str(getattr(item, "id", "")),
        "item.title": getattr(item, "title", "") or "",
        "item.url": normalize_url(getattr(item, "url", "") or ""),
        "item.canonical_url": normalize_url(getattr(item, "canonical_url", "") or ""),
        "item.summary": getattr(item, "summary", "") or "",
        "item.status": getattr(item, "status", "") or "",
        "item.published_at": isoformat(getattr(item, "published_at", None)),
        "item.first_seen_at": isoformat(getattr(item, "first_seen_at", None)),
        "alert.count": str(alert_context.count if alert_context else 0),
        "alert.primary_name": alert_context.primary_name if alert_context else "",
        "alert.names": ", ".join(alert_context.names) if alert_context else "",
        "alert.categories": ", ".join(alert_context.categories) if alert_context else "",
        "alert.matched_keywords": ", ".join(alert_context.matched_keywords) if alert_context else "",
        "failed_webhook.id": str(failed_webhook_context.id) if failed_webhook_context else "",
        "failed_webhook.name": failed_webhook_context.name if failed_webhook_context else "",
        "failed_webhook.event_type": failed_webhook_context.event_type if failed_webhook_context else "",
        "failed_webhook.status_code": str(failed_webhook_context.status_code or "") if failed_webhook_context else "",
        "failed_webhook.error": (failed_webhook_context.error or "") if failed_webhook_context else "",
        "failed_webhook.attempted_at": isoformat(failed_webhook_context.attempted_at if failed_webhook_context else None),
        "digest.window_start": isoformat(digest_context.window_start if digest_context else None),
        "digest.window_end": isoformat(digest_context.window_end if digest_context else None),
        "digest.total_items": str(digest_context.total_items if digest_context else 0),
        "digest.total_feeds": str(digest_context.total_feeds if digest_context else 0),
        "digest.feed_names": ", ".join(digest_context.feed_names) if digest_context else "",
        "digest.top_titles": "\n".join(digest_context.top_titles) if digest_context else "",
        "brief.id": str(digest_context.brief_id) if digest_context and digest_context.brief_id else "",
        "brief.date": digest_context.brief_date if digest_context and digest_context.brief_date else "",
        "brief.generated_at": isoformat(digest_context.generated_at if digest_context else None),
        "brief.window_start": isoformat(digest_context.window_start if digest_context else None),
        "brief.window_end": isoformat(digest_context.window_end if digest_context else None),
        "brief.title": digest_context.title if digest_context else "",
        "brief.title_html": escape(digest_context.title) if digest_context else "",
        "brief.url": digest_context.brief_url if digest_context else "",
        "brief.url_html": escape(digest_context.brief_url, quote=True) if digest_context else "",
        "brief.text": digest_context.brief_text if digest_context else "",
        "brief.text_html": _html_text(digest_context.brief_text) if digest_context else "",
        "brief.item_count": str(digest_context.total_items if digest_context else 0),
        "brief.key_points": "\n".join(digest_context.key_points or []) if digest_context else "",
        "brief.key_points_html": _html_lines(digest_context.key_points or []) if digest_context else "",
        "brief.recommended_actions": "\n".join(digest_context.recommended_actions or []) if digest_context else "",
        "brief.recommended_actions_html": _html_lines(digest_context.recommended_actions or []) if digest_context else "",
    }


def render_field(field: NotificationWebhookField, context: dict[str, str]) -> NotificationWebhookField:
    return NotificationWebhookField(
        key=render_template(field.key, context),
        value=render_template(field.value, context),
    )


def render_template(template: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        variable_name = match.group(1).strip()
        if variable_name not in context:
            raise TemplateRenderError(f"Unknown template variable: {variable_name}")
        return context[variable_name]

    return TEMPLATE_PATTERN.sub(replace, template)


def assign_nested_json_value(target: dict, key_path: str, value: str) -> None:
    if not key_path.strip():
        raise TemplateRenderError("JSON body field key cannot be empty")

    parts = [part for part in key_path.split(".") if part]
    if not parts:
        raise TemplateRenderError("JSON body field key cannot be empty")

    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if existing is None:
            existing = {}
            cursor[part] = existing
        elif not isinstance(existing, dict):
            raise TemplateRenderError(f"Conflicting JSON body field path: {key_path}")
        cursor = existing
    cursor[parts[-1]] = value


def isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _html_text(value: str) -> str:
    return escape(value).replace("\n", "<br>")


def _html_lines(values: list[str]) -> str:
    return "<br>".join(escape(value) for value in values)
