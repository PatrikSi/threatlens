"""Read evidence-quality caveats without materializing unbounded report metadata."""

import json
import uuid

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.models.report import Report

_COVERAGE_CHARACTER_LIMIT = 8192


def report_quality_excerpt(db: Session, report_id: uuid.UUID) -> tuple[dict, bool]:
    raw, grounding_status = db.execute(
        select(
            func.substr(cast(Report.coverage_json, String), 1, _COVERAGE_CHARACTER_LIMIT + 1),
            func.substr(Report.coverage_json["grounding"]["status"].as_string(), 1, 80),
        ).where(Report.id == report_id)
    ).one()
    if raw is None:
        return {}, False
    if len(raw) <= _COVERAGE_CHARACTER_LIMIT:
        coverage = json.loads(raw)
        if isinstance(coverage, dict):
            return coverage, False
    # Never make a bounded read look fully grounded by omitting its caveats.
    # Preserve the small status separately even when legacy metadata is large.
    return {
        "grounding": {"status": grounding_status or "unavailable"},
        "warnings": [
            "Evidence-quality details are incomplete in this response. Open the report in ThreatLens to review the saved coverage warnings."
        ],
        "details_truncated": True,
    }, True
