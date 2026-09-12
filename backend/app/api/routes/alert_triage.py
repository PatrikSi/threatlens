from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.db.session import get_db
from app.models.user import User
from app.models.alert_occurrence import AlertOccurrence
from app.schemas.alert import AlertOccurrenceResponse
from app.services.alert_occurrences import (
    AlertOccurrenceConflictError,
    AlertOccurrenceNotFoundError,
)
from app.services.alert_triage import update_assignment, update_due_date
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.data_access_policy import DataAccessContext

router = APIRouter(prefix="/occurrences")


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    action: Literal["claim", "unclaim", "assign"]
    assignee_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.action != "assign" and self.assignee_user_id is not None:
            raise ValueError("An assignee is supplied only with the assign action.")
        return self


class DeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    due_at: datetime | None
    escalation_after_minutes: int | None = Field(default=None, ge=0, le=525600)

    @field_validator("due_at")
    @classmethod
    def normalize_due_at(cls, value: datetime | None) -> datetime | None:
        return (
            value.replace(tzinfo=timezone.utc)
            if value is not None and value.tzinfo is None
            else value
        )

    @model_validator(mode="after")
    def validate_escalation(self):
        if self.due_at is None and self.escalation_after_minutes is not None:
            raise ValueError("An escalation delay requires a due date.")
        return self


@router.patch("/{occurrence_id}/assignment", response_model=AlertOccurrenceResponse)
def patch_alert_assignment(
    occurrence_id: uuid.UUID,
    payload: AssignmentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("write:alerts", "write:teams", "read:items")
    ),
    authorization: AuthorizationContext | None = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    try:
        occurrence = update_assignment(
            db,
            user=user,
            authorization=authorization,
            data_access=data_access,
            occurrence_id=occurrence_id,
            **payload.model_dump(),
        )
        _audit_action(
            db, user=user, occurrence=occurrence, action="alerts.occurrence.assignment"
        )
        db.commit()
        db.refresh(occurrence)
        return occurrence
    except (AlertOccurrenceConflictError, AlertOccurrenceNotFoundError) as exc:
        _raise_triage_error(db, exc)


@router.patch("/{occurrence_id}/deadline", response_model=AlertOccurrenceResponse)
def patch_alert_deadline(
    occurrence_id: uuid.UUID,
    payload: DeadlineRequest,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_permissions("write:alerts", "write:teams", "read:items")
    ),
    authorization: AuthorizationContext | None = Depends(get_authorization_context),
    data_access: DataAccessContext = Depends(get_data_access_context),
):
    try:
        occurrence = update_due_date(
            db,
            user=user,
            authorization=authorization,
            data_access=data_access,
            occurrence_id=occurrence_id,
            **payload.model_dump(),
        )
        _audit_action(
            db, user=user, occurrence=occurrence, action="alerts.occurrence.deadline"
        )
        db.commit()
        db.refresh(occurrence)
        return occurrence
    except (AlertOccurrenceConflictError, AlertOccurrenceNotFoundError) as exc:
        _raise_triage_error(db, exc)


def _audit_action(
    db: Session, *, user: User, occurrence: AlertOccurrence, action: str
) -> None:
    record_audit(
        db,
        actor_user_id=user.id,
        action=action,
        resource_type="alert_occurrence",
        resource_id=str(occurrence.id),
        metadata={
            "team_id": str(occurrence.team_id),
            "version": occurrence.version,
            "assignee_user_id": str(occurrence.assignee_user_id)
            if occurrence.assignee_user_id
            else None,
            "due_at": occurrence.due_at.isoformat() if occurrence.due_at else None,
        },
    )


def _raise_triage_error(
    db: Session, exc: AlertOccurrenceConflictError | AlertOccurrenceNotFoundError
) -> None:
    db.rollback()
    headers = (
        {"X-Current-Version": str(exc.current_version)}
        if isinstance(exc, AlertOccurrenceConflictError)
        else None
    )
    raise ApiHTTPException(
        status_code=409 if isinstance(exc, AlertOccurrenceConflictError) else 404,
        detail=str(exc),
        error_code=exc.code,
        headers=headers,
    ) from exc
