"""Shared alert object access and canonical team-before-resource locking."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.alert_interest import AlertInterest
from app.models.user import User
from app.schemas.alert import AlertInterestUpdate
from app.services.authorization import AuthorizationContext
from app.services.team_access import require_team_access, team_access_predicate


def alert_scope_predicate(
    owner_column: ColumnElement[uuid.UUID],
    team_column: ColumnElement[uuid.UUID],
    user_id: uuid.UUID,
) -> ColumnElement[bool]:
    return or_(owner_column == user_id, team_access_predicate(team_column, user_id))


def lock_alert_teams(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext | None,
    team_ids: Iterable[uuid.UUID],
    exclusive: bool = False,
    manage: bool = False,
) -> None:
    """Personal API compatibility must never imply an unrestricted team credential."""
    for team_id in sorted(set(team_ids), key=str):
        if not isinstance(authorization, AuthorizationContext) or not (
            authorization.has("write:teams") and authorization.has("write:alerts")
        ):
            raise ApiHTTPException(
                status_code=403,
                error_code="team_alert_write_required",
                detail="Editing a team queue requires both write:teams and write:alerts permissions on this credential.",
            )
        require_team_access(
            db,
            team_id=team_id,
            user=user,
            authorization=authorization,
            for_update=exclusive,
            manage=manage,
        )


def lock_team_rule_creation_slot(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext | None,
    team_id: uuid.UUID,
) -> None:
    lock_alert_teams(
        db, user=user, authorization=authorization, team_ids=[team_id], exclusive=True
    )
    count = (
        db.scalar(
            select(func.count())
            .select_from(AlertInterest)
            .where(AlertInterest.team_id == team_id)
        )
        or 0
    )
    if count >= 100:
        raise ApiHTTPException(
            status_code=409,
            error_code="team_alert_limit",
            detail="This team already has 100 watchlists. Remove an unused watchlist before adding another.",
        )


def get_alert_rule_for_update(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext | None,
    rule_id: uuid.UUID,
) -> AlertInterest:
    scope = alert_scope_predicate(AlertInterest.user_id, AlertInterest.team_id, user.id)
    team_id = db.scalar(
        select(AlertInterest.team_id).where(AlertInterest.id == rule_id, scope)
    )
    if team_id is not None:
        lock_alert_teams(db, user=user, authorization=authorization, team_ids=[team_id])
    row = db.scalar(
        select(AlertInterest)
        .where(AlertInterest.id == rule_id, scope)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ApiHTTPException(
            status_code=404,
            error_code="alert_rule_not_found",
            detail="Watchlist not found or current membership does not permit access.",
        )
    return row


def require_team_rule_version(
    rule: AlertInterest, expected_version: int | None
) -> None:
    if rule.team_id is not None and expected_version is None:
        raise ApiHTTPException(
            status_code=422,
            error_code="team_alert_version_required",
            detail="A team watchlist requires expected_row_version. Refresh the watchlist and retry your changes.",
        )


def update_rule_deadlines(
    alert: AlertInterest, payload: AlertInterestUpdate
) -> set[str]:
    fields_set = payload.model_fields_set
    changed_fields: set[str] = set()
    due_minutes = (
        payload.due_after_minutes
        if "due_after_minutes" in fields_set
        else alert.due_after_minutes
    )
    escalation_minutes = (
        payload.escalation_after_minutes
        if "escalation_after_minutes" in fields_set
        else alert.escalation_after_minutes
    )
    if (
        due_minutes is None
        and "due_after_minutes" in fields_set
        and "escalation_after_minutes" not in fields_set
    ):
        escalation_minutes = None
    if escalation_minutes is not None and due_minutes is None:
        raise ApiHTTPException(
            status_code=422,
            error_code="alert_escalation_due_required",
            detail="An escalation delay requires a due time.",
        )
    for field, value in (
        ("due_after_minutes", due_minutes),
        ("escalation_after_minutes", escalation_minutes),
    ):
        if getattr(alert, field) != value:
            setattr(alert, field, value)
            changed_fields.add(field)
    return changed_fields
