import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_daily_brief_source_item import AIDailyBriefSourceItem
from app.models.integration import IntegrationEvent
from app.services.daily_brief_notifications import (
    DailyBriefNotificationContextError,
    daily_brief_context_from_payload,
    emit_daily_brief_ready_event,
)
from app.services.notification_webhook_templates import render_notification_template_text


def test_ready_brief_event_is_idempotent_and_contains_immutable_delivery_context(db_session):
    generated_at = datetime(2026, 7, 18, 9, 0, 12, tzinfo=timezone.utc)
    brief = AIDailyBrief(
        id=uuid.uuid4(),
        brief_date=date(2026, 7, 18),
        status="ready",
        window_start=generated_at - timedelta(hours=24),
        window_end=generated_at,
        title="Identity threats lead today's brief",
        brief_text="Identity abuse remains the most actionable development.",
        key_points_json=["Review anomalous sign-ins", "Track exposed edge services"],
        recommended_actions_json=["Validate MFA coverage"],
        top_item_ids_json=[],
        item_count=9,
        generated_at=generated_at,
    )
    db_session.add(brief)
    db_session.flush()
    db_session.add_all(
        [
            AIDailyBriefSourceItem(
                daily_brief_id=brief.id,
                item_id=None,
                included=True,
                rank=1,
                title_snapshot="Identity campaign expands",
                feed_name_snapshot="CISA",
            ),
            AIDailyBriefSourceItem(
                daily_brief_id=brief.id,
                item_id=None,
                included=True,
                rank=2,
                title_snapshot="Edge service exploitation",
                feed_name_snapshot="CISA",
            ),
        ]
    )

    first = emit_daily_brief_ready_event(db_session, brief=brief)
    second = emit_daily_brief_ready_event(db_session, brief=brief)
    context = daily_brief_context_from_payload(first.payload_json)

    assert second.id == first.id
    assert db_session.query(IntegrationEvent).count() == 1
    assert first.source_type == "ai_daily_brief"
    assert first.schema_version == 1
    assert first.payload_json["schema_version"] == 1
    assert first.payload_json["scope_key"] == "ai_daily_brief:2026-07-18"
    assert context.brief_id == brief.id
    assert context.brief_date == "2026-07-18"
    assert context.generated_at == generated_at
    assert context.title == brief.title
    assert context.brief_text == brief.brief_text
    assert context.key_points == brief.key_points_json
    assert context.recommended_actions == brief.recommended_actions_json
    assert context.total_items == 9
    assert context.total_feeds == 1
    assert context.feed_names == ["CISA"]
    assert context.top_titles == ["Identity campaign expands", "Edge service exploitation"]


def test_legacy_rolling_digest_payload_has_clear_terminal_context_error():
    with pytest.raises(
        DailyBriefNotificationContextError,
        match="Legacy rolling daily digest events cannot be delivered",
    ):
        daily_brief_context_from_payload({"scope_key": "2026-07-18"})


def test_non_ready_brief_cannot_emit_notification_event(db_session):
    brief = AIDailyBrief(
        brief_date=date(2026, 7, 18),
        status="error",
        window_start=datetime(2026, 7, 17, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 18, tzinfo=timezone.utc),
        item_count=1,
        error="provider unavailable",
    )
    db_session.add(brief)

    with pytest.raises(DailyBriefNotificationContextError, match="Only a ready AI Daily Brief"):
        emit_daily_brief_ready_event(db_session, brief=brief)

    assert db_session.query(IntegrationEvent).count() == 0


def test_ai_brief_template_variables_and_legacy_digest_aliases_render_from_same_snapshot():
    generated_at = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
    brief_id = uuid.uuid4()
    context = daily_brief_context_from_payload(
        {
            "daily_brief": {
                "id": str(brief_id),
                "date": "2026-07-18",
                "generated_at": generated_at.isoformat(),
                "window_start": (generated_at - timedelta(hours=24)).isoformat(),
                "window_end": generated_at.isoformat(),
                "title": "AI <Brief>",
                "text": "Generated narrative & context",
                "key_points": ["Point one", "Point two"],
                "recommended_actions": ["Action one"],
                "item_count": 3,
                "feed_names": ["CISA"],
                "top_titles": ["Source title"],
            }
        }
    )

    rendered = render_notification_template_text(
        "{{ brief.id }}|{{ brief.title }}|{{ brief.text }}|{{ brief.key_points }}|{{ digest.total_items }}",
        user=SimpleNamespace(id=uuid.uuid4(), email="analyst@example.com"),
        feed=None,
        item=None,
        event_type="daily_digest",
        digest_context=context,
    )

    assert rendered == f"{brief_id}|AI <Brief>|Generated narrative & context|Point one\nPoint two|3"

    html_rendered = render_notification_template_text(
        "{{ brief.title_html }}|{{ brief.text_html }}|{{ brief.key_points_html }}",
        user=SimpleNamespace(id=uuid.uuid4(), email="analyst@example.com"),
        feed=None,
        item=None,
        event_type="daily_digest",
        digest_context=context,
    )

    assert html_rendered == "AI &lt;Brief&gt;|Generated narrative &amp; context|Point one<br>Point two"
