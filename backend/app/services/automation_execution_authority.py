"""Current owner authority for retaining previously delivered intelligence."""

from collections import defaultdict
from dataclasses import dataclass
import uuid

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from app.models.automation_execution import AutomationExecution
from app.models.iam import IAMGroupMembership, IAMUserRoleAssignment
from app.models.integration import IntegrationEvent
from app.models.user import User
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    authorization_context_for_user,
)
from app.services.data_access_envelopes import data_access_envelope_predicate
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    data_access_context_for_authorization,
)
from app.services.intel_event_eligibility import IntelEventBusy
from app.services.team_access import team_access_predicate


@dataclass(frozen=True)
class OwnerAuthority:
    authorization: AuthorizationContext
    data_access: DataAccessContext


def _owner_authority(db: Session, owner_user_id: uuid.UUID) -> OwnerAuthority | None:
    owner = db.get(User, owner_user_id)
    if owner is None:
        return None
    try:
        authorization = authorization_context_for_user(db, owner)
        if not all(
            authorization.has_durable(name)
            for name in ("read:items", "write:notifications")
        ):
            return None
        return OwnerAuthority(
            authorization, data_access_context_for_authorization(db, authorization)
        )
    except (AuthorizationStateUnavailable, DataPolicyUnavailable) as exc:
        raise IntelEventBusy("Current automation owner access is unavailable") from exc


class ExecutionAuthorityBatch:
    """Invocation-local snapshots under the caller's IAM/data-policy fences.

    OIDC roles and memberships can expire while these locks are held, so their
    owners are deliberately re-evaluated per use. Team membership is always read
    against the database clock. Only UUIDs are batched: event payloads can be large
    and remain materialized one at a time by the caller.
    """

    def __init__(self, db: Session, rows: list[AutomationExecution]):
        self.db = db
        self.event_ids: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for row in rows:
            self.event_ids[row.owner_user_id].add(row.event_id)
        owners = list(self.event_ids)
        self.volatile = (
            set(
                db.scalars(
                    union(
                        select(IAMUserRoleAssignment.user_id).where(
                            IAMUserRoleAssignment.user_id.in_(owners),
                            IAMUserRoleAssignment.source == "oidc",
                        ),
                        select(IAMGroupMembership.user_id).where(
                            IAMGroupMembership.user_id.in_(owners),
                            IAMGroupMembership.source == "oidc",
                        ),
                    )
                )
            )
            if owners
            else set()
        )
        self.authorities: dict[uuid.UUID, OwnerAuthority | None] = {}
        self.allowed_events: dict[uuid.UUID, set[uuid.UUID]] = {}

    def eligible(self, *, owner_user_id: uuid.UUID, event: IntegrationEvent) -> bool:
        if owner_user_id not in self.event_ids:
            return False
        if owner_user_id in self.volatile:
            authority = _owner_authority(self.db, owner_user_id)
            allowed = None
        else:
            if owner_user_id not in self.authorities:
                # An unavailable policy is not cached as a denial.
                self.authorities[owner_user_id] = _owner_authority(
                    self.db, owner_user_id
                )
            authority = self.authorities[owner_user_id]
            if authority is not None and owner_user_id not in self.allowed_events:
                self.allowed_events[owner_user_id] = set(
                    self.db.scalars(
                        select(IntegrationEvent.id).where(
                            IntegrationEvent.id.in_(self.event_ids[owner_user_id]),
                            data_access_envelope_predicate(
                                "integration_event",
                                IntegrationEvent.id,
                                authority.data_access,
                            ),
                        )
                    )
                )
            allowed = self.allowed_events.get(owner_user_id, set())
        if authority is None:
            return False
        # Replacement candidates may be outside this scan's selected UUIDs.
        if allowed is None or event.id not in self.event_ids[owner_user_id]:
            if not self.db.scalar(
                select(
                    data_access_envelope_predicate(
                        "integration_event", event.id, authority.data_access
                    )
                )
            ):
                return False
        elif event.id not in allowed:
            return False
        team_id = event.payload_json.get("team_id")
        if team_id is None:
            return True
        if not all(
            authority.authorization.has_durable(name)
            for name in ("read:teams", "read:ai")
        ):
            return False
        try:
            team_id = uuid.UUID(team_id)
        except (ValueError, TypeError, AttributeError):
            return False
        return bool(
            self.db.scalar(select(team_access_predicate(team_id, owner_user_id)))
        )
