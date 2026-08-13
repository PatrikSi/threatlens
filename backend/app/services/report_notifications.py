from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationEvent
from app.models.report import Report
from app.models.report_section import ReportSection
from app.services.integration_events import emit_integration_event


REPORT_READY_EVENT_TYPE = "report_ready"
REPORT_READY_SCHEMA_VERSION = 1


def emit_report_ready_event(db: Session, *, report: Report) -> IntegrationEvent:
    if report.status != "ready" or report.generated_at is None:
        raise ValueError("Only a ready report can emit a report-ready integration event.")
    sections = list(
        db.scalars(
            select(ReportSection)
            .where(ReportSection.report_id == report.id)
            .order_by(ReportSection.position.asc())
        ).all()
    )
    key_points = [
        point
        for section in sections
        for point in list(section.key_points_json or [])
    ][:12]
    actions = next(
        (list(section.key_points_json or []) for section in sections if section.section_key == "recommended_actions"),
        [],
    )
    full_text = "\n\n".join(
        f"## {section.title}\n\n{section.body_markdown}"
        for section in sections
        if section.body_markdown
    )
    if report.delivery_mode == "link":
        narrative = "The intelligence report is ready. Open ThreatLens to review the sourced report."
        key_points = []
        actions = []
    elif report.delivery_mode == "full":
        narrative = full_text[:100_000]
    else:
        narrative = report.summary_text or "The intelligence report is ready."
    payload = {
        "schema_version": REPORT_READY_SCHEMA_VERSION,
        "report_id": str(report.id),
        "scope_key": f"report:{report.id}:ready",
        "report_url": f"/reporting/{report.id}",
        "delivery_mode": report.delivery_mode,
        "daily_brief": {
            "schema_version": REPORT_READY_SCHEMA_VERSION,
            "id": str(report.id),
            "date": report.period_end.date().isoformat(),
            "generated_at": report.generated_at.isoformat(),
            "window_start": report.period_start.isoformat(),
            "window_end": report.period_end.isoformat(),
            "title": report.title,
            "url": f"/reporting/{report.id}",
            "text": narrative,
            "key_points": key_points,
            "recommended_actions": actions,
            "item_count": report.included_source_count,
            "feed_names": list((report.metrics_json or {}).get("feeds", {}).keys()),
            "top_titles": [],
        },
    }
    return emit_integration_event(
        db,
        event_type=REPORT_READY_EVENT_TYPE,
        source_type="report",
        source_id=report.id,
        idempotency_key=f"report:{report.id}:ready:v{REPORT_READY_SCHEMA_VERSION}",
        payload=payload,
        schema_version=REPORT_READY_SCHEMA_VERSION,
        actor_user_id=report.owner_user_id,
    )
