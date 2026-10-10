"""Review deadlines are separate from investigation execution ownership."""

from datetime import datetime, timezone
import uuid
from sqlalchemy.orm import Session
from app.core.api_errors import ApiHTTPException
from app.models.team_hunt_claim import TeamHuntClaim
from app.schemas.team_hunt_worklist import (
    HuntReminderAcknowledgement,
    HuntReviewSchedule,
    HuntReviewScheduleCommand,
)
from app.services.audit import record_audit
from app.services.team_access import assert_current_team_access
from app.services.team_assessment_access import (
    AssessmentRequest,
    fence_assessment_request,
)
from app.services.team_assessment_audit import assessment_audit_labels
from app.services.team_hunt_review_access import lock_review_hunt


def save_review_schedule(
    db: Session,
    *,
    actor: AssessmentRequest,
    team_id: uuid.UUID,
    assessment_id: uuid.UUID,
    hunt_id: str,
    payload: HuntReviewScheduleCommand | HuntReminderAcknowledgement,
) -> HuntReviewSchedule:
    assessment, row = lock_review_hunt(
        db,
        actor=actor,
        team_id=team_id,
        assessment_id=assessment_id,
        hunt_id=hunt_id,
        expected_assessment_version=payload.expected_assessment_version,
    )
    if (row.review_version if row else 0) != payload.expected_version:
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_review_version_conflict",
            detail="The review schedule changed. Refresh this hunt before saving.",
        )
    now = datetime.now(timezone.utc)
    if isinstance(payload, HuntReminderAcknowledgement):
        if row is None or row.reminded_at is None:
            raise ApiHTTPException(
                status_code=409,
                error_code="hunt_reminder_unavailable",
                detail="This deadline has no active reminder. Refresh the hunt queue.",
            )
        row.reminder_acknowledged_at = row.reminder_acknowledged_at or now
        action = "ai.hunt.reminder.acknowledge"
    else:
        if row is None:
            row = TeamHuntClaim(
                assessment_id=assessment_id,
                hunt_id=hunt_id,
                version=1,
                review_version=0,
            )
            db.add(row)
        row.review_version += 1
        row.priority, row.review_due_at = payload.priority, payload.due_at
        row.reminded_at = row.reminder_acknowledged_at = None
        action = "ai.hunt.schedule"
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.user.id,
        action=action,
        resource_type="item",
        resource_id=str(assessment.item_id),
        data_access_governed=True,
        data_access_label_ids=assessment_audit_labels(db, assessment),
        metadata={
            "team_id": str(team_id),
            "assessment_id": str(assessment.id),
            "hunt_id": hunt_id,
            "review_version": row.review_version,
            "due_at": row.review_due_at.isoformat() if row.review_due_at else None,
            "priority": row.priority,
        },
    )
    fence_assessment_request(db, actor, write=True)
    assert_current_team_access(db, team_id=team_id, user_id=actor.user.id)
    return HuntReviewSchedule(
        version=row.review_version,
        priority=row.priority,
        due_at=row.review_due_at,
        overdue=bool(row.review_due_at and row.review_due_at <= now),
        reminded_at=row.reminded_at,
        reminder_acknowledged_at=row.reminder_acknowledged_at,
    )
