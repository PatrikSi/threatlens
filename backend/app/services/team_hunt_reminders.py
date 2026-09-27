"""Deduplicated, durable in-app reminders with bounded, concurrent-safe sweeps."""

from datetime import datetime, timezone
from sqlalchemy import case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from app.models.team import Team
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_item_assessment import TeamItemAssessment


def dispatch_hunt_review_reminders(db: Session, *, limit: int = 100) -> int:
    """One reminder per explicit schedule revision; reads remain evidence-fenced.

    No message leaves ThreatLens. The shared queue displays the reminder until
    an owner/manager acknowledges it. Repeated sweeps or redelivery cannot
    recreate an acknowledged reminder without an explicit schedule change.
    """
    claim, assessment = TeamHuntClaim, TeamItemAssessment
    raw = cast(assessment.result_json, JSONB).op("->")("hunts")
    safe = case(
        (func.jsonb_typeof(raw) == "array", raw), else_=cast(literal("[]"), JSONB)
    )
    hunts = (
        func.jsonb_array_elements(safe).table_valued("value").lateral("review_hunts")
    )
    value = cast(hunts.c.value, JSONB)
    pending = (
        select(1)
        .select_from(hunts)
        .where(
            value["id"].astext == claim.hunt_id,
            func.coalesce(value["review_status"].astext, "suggested") == "suggested",
            value["investigation_id"].astext.is_(None),
        )
        .correlate(assessment, claim)
        .exists()
    )
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(claim)
        .join(assessment, assessment.id == claim.assessment_id)
        .join(Team, Team.id == assessment.team_id)
        .where(
            Team.active.is_(True),
            claim.review_due_at <= now,
            claim.reminded_at.is_(None),
            pending,
        )
        .order_by(claim.review_due_at, claim.assessment_id, claim.hunt_id)
        .limit(min(100, max(1, limit)))
        .with_for_update(of=claim, skip_locked=True)
    ).all()
    for row in rows:
        row.reminded_at = now
    db.flush()
    return len(rows)
