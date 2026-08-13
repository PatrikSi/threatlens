import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.integration import IntegrationEvent
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services.daily_brief_notifications import daily_brief_context_from_payload
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.notification_webhook_templates import render_notification_template_text
from app.services.report_notifications import emit_report_ready_event
from app.services.report_rendering import render_report_html, render_report_markdown, render_report_pdf
from app.services.report_storage import report_detail_response


def test_report_ready_event_is_idempotent_and_uses_immutable_report_snapshot(db_session):
    report = _persist_ready_report(db_session)

    first = emit_report_ready_event(db_session, report=report)
    second = emit_report_ready_event(db_session, report=report)
    context = daily_brief_context_from_payload(first.payload_json)

    assert second.id == first.id
    assert db_session.query(IntegrationEvent).count() == 1
    assert first.event_type == "report_ready"
    assert first.source_type == "report"
    assert first.payload_json["report_url"] == f"/reporting/{report.id}"
    assert context.brief_id == report.id
    assert context.title == report.title
    assert context.brief_url == f"/reporting/{report.id}"
    assert context.total_items == 1
    assert context.key_points == ["Validate identity controls"]
    rendered_url = render_notification_template_text(
        "{{ brief.url }}|{{ brief.url_html }}",
        user=SimpleNamespace(id=uuid.uuid4(), email="analyst@example.com"),
        feed=None,
        item=None,
        event_type="report_ready",
        digest_context=context,
    )
    assert rendered_url == f"/reporting/{report.id}|/reporting/{report.id}"


def test_report_ready_is_supported_by_destination_connectors():
    assert SMTPIntegrationConnector().supports_event_type("report_ready")
    assert WebhookIntegrationConnector().supports_event_type("report_ready")


def test_report_artifacts_render_from_canonical_snapshot_and_escape_html(db_session):
    report = _persist_ready_report(db_session, title="Weekly <script>alert(1)</script>")
    detail = report_detail_response(db_session, report=report)

    markdown = render_report_markdown(detail)
    html = render_report_html(detail)
    pdf = render_report_pdf(detail)

    assert "# Weekly <script>alert(1)</script>" in markdown
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert pdf.startswith(b"%PDF")


def _persist_ready_report(db_session, *, title: str = "Weekly identity landscape") -> Report:
    generated_at = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        title=title,
        report_type="weekly_landscape",
        status="ready",
        trigger_source="manual",
        generation_stage="ready",
        period_start=generated_at - timedelta(days=7),
        period_end=generated_at,
        filters_json={},
        prompt_config_json={
            "audience": "security_team",
            "objective": "Summarize identity threats.",
            "tone": "analytical",
            "detail_level": "standard",
            "use_company_context": True,
            "focus_topics": [],
            "excluded_topics": [],
        },
        sections_config_json=[
            {"key": "executive_summary", "title": "Executive Summary", "enabled": True}
        ],
        metrics_json={"feeds": {"CISA": 1}},
        coverage_json={"coverage_percent": 100.0, "warnings": []},
        summary_text="Identity abuse remains material [S1].",
        source_count=1,
        included_source_count=1,
        citation_count=1,
        estimated_input_tokens=100,
        context_window_tokens=8192,
        model_calls=2,
        generation_batches=1,
        provider="openai_compatible",
        model="local-test",
        delivery_requested=True,
        delivery_mode="summary",
        generated_at=generated_at,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add(
        ReportSection(
            report_id=report.id,
            section_key="executive_summary",
            title="Executive Summary",
            position=1,
            status="ready",
            body_markdown="Identity abuse remains material [S1].",
            key_points_json=["Validate identity controls"],
            citations_json=["S1"],
        )
    )
    db_session.add(
        ReportSourceItem(
            report_id=report.id,
            item_id=None,
            citation_key="S1",
            included=True,
            rank=1,
            title_snapshot="Identity campaign expands",
            feed_name_snapshot="CISA",
            url_snapshot="https://example.com/report",
            first_seen_at_snapshot=generated_at - timedelta(days=1),
            tags_snapshot_json=["identity"],
            iocs_snapshot_json=[],
            evidence_text="[S1] Identity campaign expands",
            estimated_tokens=10,
        )
    )
    db_session.flush()
    return report
