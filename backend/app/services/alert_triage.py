"""Versioned triage actions and bounded, idempotent deadline escalation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.alert_occurrence import AlertOccurrence
from app.models.team import Team
from app.models.user import User
from app.services.alert_occurrences import (
    _record_activity,
    _require_expected_version,
    get_alert_occurrence,
)
from app.services.authorization import (
    AuthorizationContext,
    authorization_context_for_user,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    data_access_context_for_authorization,
)
from app.services.team_access import team_access_predicate


def update_assignment(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    occurrence_id: uuid.UUID,
    expected_version: int,
    action: str,
    assignee_user_id: uuid.UUID | None = None,
) -> AlertOccurrence:
    occurrence = get_alert_occurrence(
        db,
        user=user,
        occurrence_id=occurrence_id,
        data_access=data_access,
        for_update=True,
        authorization=authorization,
    )
    _require_expected_version(occurrence, expected_version)
    _require_open_team_occurrence(occurrence)
    previous = occurrence.assignee_user_id
    if action == "claim":
        if previous is not None and previous != user.id:
            raise ApiHTTPException(
                status_code=409,
                error_code="alert_already_claimed",
                detail="Another analyst has claimed this occurrence. Refresh the queue or ask a team manager to reassign it.",
            )
        target = user.id
    elif action == "unclaim":
        if previous is not None and previous != user.id:
            _require_manager(db, occurrence, user)
        target = None
    elif action == "assign":
        _require_manager(db, occurrence, user)
        target = assignee_user_id
    else:
        raise ApiHTTPException(
            status_code=422,
            error_code="alert_assignment_action_invalid",
            detail="Choose claim, unclaim or assign.",
        )
    if target is not None:
        _require_eligible_assignee(db, occurrence=occurrence, user_id=target)
    if target == previous:
        return occurrence
    occurrence.assignee_user_id = target
    occurrence.version += 1
    _record_activity(
        db,
        occurrence=occurrence,
        actor_user_id=user.id,
        action={"claim": "claimed", "unclaim": "unclaimed", "assign": "assigned"}[
            action
        ],
        details={
            "previous_assignee_user_id": str(previous) if previous else None,
            "assignee_user_id": str(target) if target else None,
        },
    )
    db.flush()
    return occurrence


def update_due_date(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    occurrence_id: uuid.UUID,
    expected_version: int,
    due_at: datetime | None,
    escalation_after_minutes: int | None,
) -> AlertOccurrence:
    occurrence = get_alert_occurrence(
        db,
        user=user,
        occurrence_id=occurrence_id,
        data_access=data_access,
        for_update=True,
        authorization=authorization,
    )
    _require_expected_version(occurrence, expected_version)
    _require_open_team_occurrence(occurrence)
    _require_manager(db, occurrence, user)
    if due_at is None and escalation_after_minutes is not None:
        raise ApiHTTPException(
            status_code=422,
            error_code="alert_escalation_due_required",
            detail="An escalation delay requires a due date.",
        )
    if (
        occurrence.due_at == due_at
        and occurrence.escalation_after_minutes == escalation_after_minutes
    ):
        return occurrence
    occurrence.due_at = due_at
    occurrence.escalation_after_minutes = escalation_after_minutes
    occurrence.escalated_at = None
    occurrence.version += 1
    _record_activity(
        db,
        occurrence=occurrence,
        actor_user_id=user.id,
        action="deadline_changed",
        details={
            "due_at": due_at.isoformat() if due_at else None,
            "escalation_after_minutes": escalation_after_minutes,
        },
    )
    db.flush()
    return occurrence


def escalate_overdue_alerts(
    db: Session, *, now: datetime | None = None, limit: int = 100
) -> int:
    """One event per deadline revision; parallel sweeps skip claimed rows."""
    current_time = now or datetime.now(timezone.utc)
    active_team = (
        select(Team.id)
        .where(Team.id == AlertOccurrence.team_id, Team.active.is_(True))
        .exists()
    )
    rows = db.scalars(
        select(AlertOccurrence)
        .where(
            active_team,
            AlertOccurrence.lifecycle_state != "closed",
            AlertOccurrence.escalated_at.is_(None),
            AlertOccurrence.due_at.is_not(None),
            AlertOccurrence.escalation_after_minutes.is_not(None),
            AlertOccurrence.due_at
            + func.make_interval(
                0, 0, 0, 0, 0, AlertOccurrence.escalation_after_minutes
            )
            <= current_time,
            or_(
                AlertOccurrence.snoozed_until.is_(None),
                AlertOccurrence.snoozed_until <= current_time,
            ),
        )
        .order_by(AlertOccurrence.due_at, AlertOccurrence.id)
        .limit(max(1, min(limit, 500)))
        .with_for_update(skip_locked=True, of=AlertOccurrence)
        .execution_options(populate_existing=True)
    ).all()
    for occurrence in rows:
        occurrence.escalated_at = current_time
        occurrence.version += 1
        _record_activity(
            db,
            occurrence=occurrence,
            actor_user_id=None,
            action="escalated",
            details={
                "due_at": occurrence.due_at.isoformat(),
                "escalation_after_minutes": occurrence.escalation_after_minutes,
                "audience": "team_managers",
            },
        )
    db.flush()
    return len(rows)


def _require_open_team_occurrence(occurrence: AlertOccurrence) -> None:
    if occurrence.team_id is None or occurrence.lifecycle_state == "closed":
        raise ApiHTTPException(
            status_code=422,
            error_code="team_triage_unavailable",
            detail="Assignment and deadline changes require an open team occurrence.",
        )


def _require_manager(db: Session, occurrence: AlertOccurrence, user: User) -> None:
    if not db.scalar(
        select(team_access_predicate(occurrence.team_id, user.id, manage=True))
    ):
        raise ApiHTTPException(
            status_code=403,
            error_code="team_manager_required",
            detail="A current team manager must change assignees or deadlines. Members can claim and release their own work.",
        )


def _require_eligible_assignee(
    db: Session, *, occurrence: AlertOccurrence, user_id: uuid.UUID
) -> None:
    target = db.scalar(
        select(User).where(
            User.id == user_id, team_access_predicate(occurrence.team_id, User.id)
        )
    )
    eligible = False
    if target is not None:
        # This is the target's eligibility, never a replacement for caller authorization.
        target_authorization = authorization_context_for_user(db, target)
        target_access = data_access_context_for_authorization(db, target_authorization)
        eligible = all(
            target_authorization.has(permission)
            for permission in ("write:teams", "write:alerts", "read:items")
        ) and bool(
            db.scalar(
                select(
                    data_access_envelope_predicate(
                        DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
                        occurrence.id,
                        target_access,
                    )
                )
            )
        )
    if not eligible:
        raise ApiHTTPException(
            status_code=422,
            error_code="alert_assignee_ineligible",
            detail="The assignee must be a current team member with triage permissions and access to this occurrence's evidence.",
        )
