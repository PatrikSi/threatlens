"""Page retained passages in SQL while pinning their report and source revisions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.report import Report
from app.models.report_source_item import ReportSourceItem
from app.schemas.report_evidence import ReportSourceEvidenceResponse


def retained_source_evidence(
    db: Session,
    *,
    report: Report,
    citation_key: str,
    editorial_version: int,
    offset: int,
    limit: int,
    source_revision: str | None,
) -> ReportSourceEvidenceResponse:
    """Read a page after the caller locks and authorizes the parent report.

    Offsets count Unicode characters, matching PostgreSQL's substring operation.
    A source digest pins subsequent pages even if retained text changes without
    a corresponding editorial version change, as can occur in legacy records.
    """
    if report.editorial_version != editorial_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="report_evidence_revision_changed",
            detail="The report revision changed. Refresh the report before reviewing its evidence.",
        )
    if offset and source_revision is None:
        raise ApiHTTPException(
            status_code=422,
            error_code="report_evidence_revision_required",
            detail="Continue using the source revision returned with the first passage.",
        )
    retained_text = func.coalesce(ReportSourceItem.evidence_text, "")
    source_digest = func.encode(
        func.sha256(func.convert_to(retained_text, "UTF8")), "hex"
    )
    statement = (
        select(
            func.substr(retained_text, offset + 1, limit).label("passage"),
            func.char_length(retained_text).label("characters"),
            source_digest.label("revision"),
        )
        .where(
            ReportSourceItem.report_id == report.id,
            ReportSourceItem.citation_key == citation_key,
        )
        .with_for_update(read=True, of=ReportSourceItem)
    )
    row = db.execute(statement).one_or_none()
    if row is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="report_evidence_not_found",
            detail="Retained report source not found.",
        )
    if source_revision is not None and source_revision != row.revision:
        raise ApiHTTPException(
            status_code=409,
            error_code="report_evidence_revision_changed",
            detail="The retained passage changed. Refresh the report and reopen its evidence.",
        )
    if offset > row.characters:
        raise ApiHTTPException(
            status_code=422,
            error_code="report_evidence_offset_invalid",
            detail="The passage offset exceeds the retained source length.",
        )
    end = offset + len(row.passage)
    return ReportSourceEvidenceResponse(
        report_id=report.id,
        citation_key=citation_key,
        editorial_version=report.editorial_version,
        source_revision=row.revision,
        evidence_text=row.passage,
        total_characters=row.characters,
        offset=offset,
        next_offset=end if end < row.characters else None,
    )
