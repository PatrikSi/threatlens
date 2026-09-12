"""Human report transitions share one locked revision and one audit commit."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.schemas.report_editorial import ReportDraftUpdate, ReportEditorialTransition
from app.services.audit import record_audit
from app.services.report_grounding import CITATION_PATTERN
from app.services.report_notifications import emit_report_ready_event
from app.services.report_revision import report_revision_hash


class ReportEditorialError(ValueError):
    def __init__(self, message: str, *, code: str = "report_editorial_conflict"):
        self.code = code
        super().__init__(message)


def _require_version(report: Report, expected_version: int) -> None:
    if report.editorial_version != expected_version:
        raise ReportEditorialError(
            "The report changed since you opened it. Reload the report before saving or reviewing it."
        )
    if report.status != "ready":
        raise ReportEditorialError(
            "Wait for report generation to complete before editing or reviewing it."
        )


def _clear_review(report: Report) -> None:
    report.review_revision_hash = None
    report.approved_revision_hash = None
    report.review_submitted_at = None
    report.review_submitted_by_user_id = None
    report.approved_at = None
    report.approved_by_user_id = None
    report.approval_self_review = False


def update_report_draft(
    db: Session, *, report: Report, payload: ReportDraftUpdate, actor_user_id: uuid.UUID
) -> None:
    _require_version(report, payload.expected_version)
    if report.publication_status != "draft":
        raise ReportEditorialError(
            "Return this report to draft before editing it. Published reports are immutable."
        )
    sections = {
        section.section_key: section
        for section in db.scalars(
            select(ReportSection).where(ReportSection.report_id == report.id)
        )
    }
    if set(sections) != {section.key for section in payload.sections}:
        raise ReportEditorialError(
            "The report's section set changed. Reload the report before editing it."
        )
    for submitted in payload.sections:
        section = sections[submitted.key]
        if section.body_markdown != submitted.body_markdown:
            section.key_points_json = []
            section.citations_json = sorted(
                set(CITATION_PATTERN.findall(submitted.body_markdown))
            )
        section.title = submitted.title.strip()
        section.body_markdown = submitted.body_markdown
    report.title = payload.title.strip()
    report.summary_text = payload.summary_text
    report.last_edited_at = datetime.now(timezone.utc)
    report.last_edited_by_user_id = actor_user_id
    report.editorial_version += 1
    _clear_review(report)
    # Generated grounding counters describe the original machine narrative.
    coverage = dict(report.coverage_json or {})
    coverage["human_edited"] = True
    coverage["grounding"] = {"version": 1, "status": "human_edited"}
    report.coverage_json = coverage
    _audit(db, report=report, action="edit", actor_user_id=actor_user_id)


def _validate_references(db: Session, report: Report) -> None:
    known = set(
        db.scalars(
            select(ReportSourceItem.citation_key).where(
                ReportSourceItem.report_id == report.id,
                ReportSourceItem.included.is_(True),
            )
        )
    )
    for section in db.execute(
        select(ReportSection.title, ReportSection.body_markdown).where(
            ReportSection.report_id == report.id
        )
    ):
        if not section.body_markdown.strip():
            raise ReportEditorialError(
                f"Complete section '{section.title}' before submitting the report.",
                code="report_draft_incomplete",
            )
        if set(CITATION_PATTERN.findall(section.body_markdown)) - known:
            raise ReportEditorialError(
                f"Section '{section.title}' refers to source citations absent from this report. Correct them before review.",
                code="report_citation_unknown",
            )
    if set(CITATION_PATTERN.findall(report.summary_text or "")) - known:
        raise ReportEditorialError(
            "The report summary refers to unavailable source citations.",
            code="report_citation_unknown",
        )


def transition_report(
    db: Session,
    *,
    report: Report,
    payload: ReportEditorialTransition,
    actor_user_id: uuid.UUID,
) -> uuid.UUID | None:
    _require_version(report, payload.expected_version)
    allowed = {
        "submit": {"draft"},
        "return_to_draft": {"review", "approved"},
        "approve": {"review"},
        "publish": {"approved"},
    }
    if report.publication_status not in allowed[payload.action]:
        raise ReportEditorialError(
            f"This action is unavailable while the report is {report.publication_status}. Reload its current state."
        )
    current_time = datetime.now(timezone.utc)
    event_id = None
    if payload.action == "return_to_draft":
        _clear_review(report)
        report.publication_status = "draft"
    else:
        _validate_references(db, report)
        revision = report_revision_hash(db, report)
        if payload.action == "submit":
            report.publication_status = "review"
            report.review_revision_hash = revision
            report.review_submitted_at = current_time
            report.review_submitted_by_user_id = actor_user_id
        elif payload.action == "approve":
            if report.review_revision_hash != revision:
                raise ReportEditorialError(
                    "The report content or evidence changed after submission. Return it to draft and submit the current revision."
                )
            report.publication_status = "approved"
            report.approved_revision_hash = revision
            report.approved_at = current_time
            report.approved_by_user_id = actor_user_id
            report.approval_self_review = actor_user_id in {
                report.owner_user_id,
                report.last_edited_by_user_id,
            }
        else:
            if (
                report.approved_revision_hash != revision
                or report.review_revision_hash != revision
            ):
                raise ReportEditorialError(
                    "The report no longer matches its approved content and evidence. Return it to draft for another review."
                )
            report.publication_status = "published"
            report.published_revision_hash = revision
            report.published_at = current_time
            report.published_by_user_id = actor_user_id
    report.editorial_version += 1
    report.editorial_note = payload.note.strip() if payload.note else None
    db.flush()
    if payload.action == "publish" and report.delivery_requested:
        event_id = emit_report_ready_event(db, report=report).id
    _audit(db, report=report, action=payload.action, actor_user_id=actor_user_id)
    return event_id


def _audit(
    db: Session, *, report: Report, action: str, actor_user_id: uuid.UUID
) -> None:
    record_audit(
        db,
        actor_user_id=actor_user_id,
        action=f"reports.editorial.{action}",
        resource_type="report",
        resource_id=str(report.id),
        metadata={
            "publication_status": report.publication_status,
            "editorial_version": report.editorial_version,
            "review_required": report.review_required,
            "revision_hash": report.published_revision_hash
            or report.approved_revision_hash
            or report.review_revision_hash,
            "self_review": report.approval_self_review,
        },
    )
