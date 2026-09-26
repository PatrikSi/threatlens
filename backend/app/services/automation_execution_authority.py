"""Current owner authority for retaining previously delivered intelligence."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationEvent
from app.models.user import User
from app.services.authorization import (
    AuthorizationStateUnavailable,
    authorization_context_for_user,
)
from app.services.data_access_envelopes import data_access_envelope_predicate
from app.services.data_access_policy import (
    DataPolicyUnavailable,
    data_access_context_for_authorization,
)
from app.services.intel_event_eligibility import IntelEventBusy
from app.services.team_access import team_access_predicate


def execution_owner_eligible(
    db: Session, *, owner_user_id: uuid.UUID, event: IntegrationEvent
) -> bool:
    """Caller fences IAM/data policy before execution locks; this acquires no locks.

    An unavailable policy is retryable, not evidence that access was revoked.
    Current team membership and all retained handling labels remain mandatory.
    """
    owner = db.get(User, owner_user_id)
    if owner is None:
        return False
    try:
        authorization = authorization_context_for_user(db, owner)
        if not all(
            authorization.has_durable(name)
            for name in ("read:items", "write:notifications")
        ):
            return False
        access = data_access_context_for_authorization(db, authorization)
        if not db.scalar(
            select(
                data_access_envelope_predicate("integration_event", event.id, access)
            )
        ):
            return False
        team_id = event.payload_json.get("team_id")
        if team_id is None:
            return True
        if not all(
            authorization.has_durable(name) for name in ("read:teams", "read:ai")
        ):
            return False
        try:
            team_id = uuid.UUID(team_id)
        except (ValueError, TypeError, AttributeError):
            return False
        return bool(db.scalar(select(team_access_predicate(team_id, owner_user_id))))
    except (AuthorizationStateUnavailable, DataPolicyUnavailable) as exc:
        raise IntelEventBusy("Current automation owner access is unavailable") from exc
