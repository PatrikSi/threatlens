"""Apply current team review and exclusions to a bounded article snapshot."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.intel_assessment import IndicatorAssessment, IndicatorSuppression
from app.services.indicator_assessments import expired
from app.services.indicator_lineage import assessment_labels_by_id


def team_indicator_snapshot(
    db: Session,
    *,
    team_id: uuid.UUID,
    item_id: uuid.UUID,
    indicators: list[dict],
    source_revision: int,
    extraction_revision: int,
) -> tuple[list[dict], str]:
    result = deepcopy(indicators)
    ids = [uuid.UUID(entry["id"]) for entry in result]
    if not ids:
        return result, hashlib.sha256(b"[]").hexdigest()
    assessments = {
        str(row.ioc_id): row
        for row in db.scalars(
            select(IndicatorAssessment).where(
                IndicatorAssessment.team_id == team_id,
                IndicatorAssessment.item_id == item_id,
                IndicatorAssessment.ioc_id.in_(ids),
            )
        )
    }
    lineage = assessment_labels_by_id(db, [row.id for row in assessments.values()])
    keys = [(row["type"], row["value"]) for row in result]
    suppressions = {
        (row.ioc_type, row.value_norm): row
        for row in db.scalars(
            select(IndicatorSuppression).where(
                IndicatorSuppression.team_id == team_id,
                tuple_(
                    IndicatorSuppression.ioc_type, IndicatorSuppression.value_norm
                ).in_(keys),
            )
        )
    }
    policy = []
    for entry in result:
        assessment = assessments.get(entry["id"])
        suppression = suppressions.get((entry["type"], entry["value"]))
        current = bool(
            assessment
            and not expired(assessment.expires_at)
            and assessment.source_revision == source_revision
            and assessment.extraction_revision == extraction_revision
        )
        suppressed = bool(
            suppression and suppression.active and not expired(suppression.expires_at)
        )
        policy.append(
            {
                "id": entry["id"],
                "verdict": assessment.verdict if current else None,
                "suppressed": suppressed,
                "assessment_label_ids": sorted(
                    str(label) for label in lineage.get(assessment.id, set())
                )
                if current
                else [],
            }
        )
        if current:
            labels = lineage.get(assessment.id, set())
            entry["assessment_label_ids"] = sorted(str(label) for label in labels)
            entry["assessment_lineage_complete"] = (
                assessment.handling_label_id in labels
            )
            entry["analyst_verdict"] = assessment.verdict
            entry["analyst_assessment_version"] = assessment.version
            if assessment.verdict in {"benign", "reference", "example", "retracted"}:
                entry["reasons"].append(f"analyst_{assessment.verdict}")
            elif assessment.verdict == "malicious":
                entry["reasons"] = [
                    reason
                    for reason in entry["reasons"]
                    if not reason.startswith("ai_")
                ]
        if suppressed:
            entry["reasons"].append("team_suppression")
        entry["excluded"] = bool(entry["reasons"])
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result, digest
