"""Common publication gate for generation, event routing, and outbound delivery."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report import Report
from app.services.report_revision import report_revision_hash


class ReportPublicationError(ValueError):
    pass


def publish_automatic_report(db: Session, report: Report) -> None:
    if report.review_required:
        raise ReportPublicationError(
            "This report requires editorial approval before publication."
        )
    report.publication_status = "published"
    report.published_at = datetime.now(timezone.utc)
    report.published_by_user_id = None
    report.published_revision_hash = report_revision_hash(db, report)


def require_report_publication(db: Session, report: Report) -> None:
    if report.status != "ready" or report.publication_status != "published":
        raise ReportPublicationError(
            "The report has not been published. Complete its editorial review before delivery."
        )
    if report.published_revision_hash is None:
        if report.review_required or report.editorial_contract_version != 0:
            raise ReportPublicationError(
                "The report is missing its approved publication revision."
            )
        return  # Explicitly migrated publication, not inferred human approval.
    if report_revision_hash(db, report) != report.published_revision_hash:
        raise ReportPublicationError(
            "The report content or evidence changed after publication. Its original publication cannot be delivered."
        )
    if (
        report.review_required
        and report.approved_revision_hash != report.published_revision_hash
    ):
        raise ReportPublicationError(
            "The published report does not match its approved revision."
        )


def publication_snapshot(report: Report) -> dict[str, object]:
    return {
        "version": 1,
        "editorial_version": report.editorial_version,
        "revision_hash": report.published_revision_hash,
        "review_required": report.review_required,
    }


def validate_event_publication(
    db: Session,
    *,
    report_id: uuid.UUID,
    payload: dict,
    schema_version: int,
    locked_report: Report | None = None,
) -> None:
    if locked_report is not None and locked_report.id != report_id:
        raise ReportPublicationError(
            "The publication check does not match its source report."
        )
    snapshot = payload.get("publication")
    if snapshot is None:
        if schema_version >= 3:
            raise ReportPublicationError(
                "The delivery is missing its report publication revision."
            )
        # Retained pre-feature events already contain immutable delivery content.
        # A modern report can never acquire this bypass through a missing marker.
        report = locked_report or db.scalar(
            select(Report)
            .where(Report.id == report_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if report is not None and (
            report.editorial_contract_version != 0
            or report.review_required
            or report.published_revision_hash is not None
        ):
            raise ReportPublicationError(
                "The delivery is missing its report publication revision."
            )
        return
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise ReportPublicationError(
            "The delivery has an unsupported report publication revision."
        )
    report = locked_report or db.scalar(
        select(Report)
        .where(Report.id == report_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if report is None:
        raise ReportPublicationError("The published source report no longer exists.")
    require_report_publication(db, report)
    if snapshot != publication_snapshot(report):
        raise ReportPublicationError(
            "The delivery no longer matches the report's publication revision."
        )
