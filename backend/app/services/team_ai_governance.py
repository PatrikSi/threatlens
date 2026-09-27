"""Team AI destination policy, checked under the team lock before provider I/O.

Unconfigured teams inherit global routing. Configured policies explicitly allow
provider identities; label rules further intersect that list. Selection changes
affect new work only, while policy revocation applies to already queued work.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.ai_provider import AIProviderConfiguration
from app.models.data_policy import HandlingLabel
from app.models.team import Team
from app.models.team_ai_governance import TeamAIGovernance
from app.models.user import User
from app.schemas.team_ai_governance import (
    TeamAIDestination,
    TeamAIGovernancePolicy,
    TeamAIGovernanceResponse,
    TeamAISelection,
)
from app.services.ai_provider_client import AIIntegrationError
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext, fence_authorization_context
from app.services.team_access import (
    assert_current_team_access,
    require_team_access,
    team_access_predicate,
)


if TYPE_CHECKING:
    from app.services.ai_config import ActiveAISettings


def governance_snapshot(db: Session, team_id: uuid.UUID) -> TeamAIGovernanceResponse:
    if db.get(Team, team_id) is None:
        raise ApiHTTPException(
            status_code=404, error_code="team_not_found", detail="Team not found."
        )
    row = db.get(TeamAIGovernance, team_id, populate_existing=True)
    keys = row.approved_provider_keys if row else []
    profiles = (
        {
            f"profile:{profile.id}": profile
            for profile in db.scalars(
                select(AIProviderConfiguration).where(
                    AIProviderConfiguration.id.in_(
                        [
                            uuid.UUID(key[8:])
                            for key in keys
                            if key.startswith("profile:")
                        ]
                    ),
                )
            )
        }
        if keys
        else {}
    )
    return TeamAIGovernanceResponse(
        team_id=team_id,
        version=row.version if row else 0,
        configured=row is not None,
        approved_provider_keys=keys,
        selected_provider_key=row.selected_provider_key if row else None,
        label_destinations=row.label_destinations if row else {},
        destinations=[
            TeamAIDestination(
                key=key,
                name="Legacy provider settings"
                if key == "legacy"
                else profiles[key].name
                if key in profiles
                else "Removed provider",
                available=key == "legacy"
                or bool(key in profiles and profiles[key].enabled),
            )
            for key in keys
        ],
    )


def read_governance(
    db: Session, *, team_id: uuid.UUID, user: User, authorization: AuthorizationContext
) -> TeamAIGovernanceResponse:
    require_team_access(db, team_id=team_id, user=user, authorization=authorization)
    manager = bool(
        db.scalar(select(team_access_predicate(team_id, user.id, manage=True)))
    )
    response = governance_snapshot(db, team_id)
    assert_current_team_access(db, team_id=team_id, user_id=user.id)
    return response.model_copy(
        update={"can_manage": manager and authorization.has("write:teams")}
    )


def save_governance(
    db: Session,
    *,
    team_id: uuid.UUID,
    user: User,
    authorization: AuthorizationContext,
    payload: TeamAIGovernancePolicy | TeamAISelection,
    approve: bool,
) -> TeamAIGovernanceResponse:
    if approve:
        fence_authorization_context(db, authorization)
        team = db.scalar(select(Team).where(Team.id == team_id).with_for_update())
        if team is None:
            raise ApiHTTPException(
                status_code=404, error_code="team_not_found", detail="Team not found."
            )
    else:
        require_team_access(
            db,
            team_id=team_id,
            user=user,
            authorization=authorization,
            manage=True,
            for_update=True,
        )
    row = db.get(TeamAIGovernance, team_id, populate_existing=True)
    if payload.expected_version != (row.version if row else 0):
        raise ApiHTTPException(
            status_code=409,
            error_code="team_ai_policy_conflict",
            detail="Team AI policy changed. Reload it before saving.",
        )
    if isinstance(payload, TeamAIGovernancePolicy):
        if not approve:
            raise ApiHTTPException(
                status_code=403,
                error_code="team_ai_approval_required",
                detail="An AI administrator must approve destinations.",
            )
        keys = set(payload.approved_provider_keys)
        all_keys = keys | {
            key for values in payload.label_destinations.values() for key in values
        }
        ids = {uuid.UUID(key[8:]) for key in all_keys if key != "legacy"}
        known = set(
            db.scalars(
                select(AIProviderConfiguration.id).where(
                    AIProviderConfiguration.id.in_(ids)
                )
            )
        )
        labels = set(
            db.scalars(
                select(HandlingLabel.id).where(
                    HandlingLabel.id.in_(payload.label_destinations),
                    HandlingLabel.is_active.is_(True),
                )
            )
        )
        if known != ids or labels != set(payload.label_destinations):
            raise ApiHTTPException(
                status_code=422,
                error_code="team_ai_destination_missing",
                detail="A selected provider or active handling label no longer exists. Reload the policy.",
            )
        if any(
            not set(values).issubset(keys)
            for values in payload.label_destinations.values()
        ):
            raise ApiHTTPException(
                status_code=422,
                error_code="team_ai_destination_unapproved",
                detail="Handling rules may only narrow approved destinations.",
            )
    else:
        keys = set(row.approved_provider_keys) if row else set()
    if (
        payload.selected_provider_key is not None
        and payload.selected_provider_key not in keys
    ):
        raise ApiHTTPException(
            status_code=422,
            error_code="team_ai_destination_unapproved",
            detail="Choose an approved destination or inherit the installation route.",
        )
    if row is None:
        row = TeamAIGovernance(team_id=team_id, version=0)
        db.add(row)
    if isinstance(payload, TeamAIGovernancePolicy):
        row.approved_provider_keys = payload.approved_provider_keys
        row.label_destinations = {
            str(key): value for key, value in payload.label_destinations.items()
        }
    row.selected_provider_key = payload.selected_provider_key
    row.version += 1
    row.updated_by_user_id = user.id
    db.flush()
    if not approve:
        assert_current_team_access(db, team_id=team_id, user_id=user.id, manage=True)
    record_audit(
        db,
        actor_user_id=user.id,
        action="teams.ai_policy.approve" if approve else "teams.ai_destination.select",
        resource_type="team",
        resource_id=str(team_id),
        metadata={"version": row.version},
    )
    return governance_snapshot(db, team_id).model_copy(
        update={"can_manage": not approve, "can_approve": approve}
    )


def team_selected_provider(db: Session, team_id: uuid.UUID) -> str | None:
    row = db.get(TeamAIGovernance, team_id)
    return row.selected_provider_key if row else None


def assert_team_ai_destination(
    db: Session, *, team_id: uuid.UUID, provider_key: str, label_ids: set[uuid.UUID]
) -> None:
    # The caller enters IAM/data/actor fences first. A shared team lock protects
    # even an absent policy row; writers take the corresponding exclusive lock.
    team = db.scalar(
        select(Team)
        .where(Team.id == team_id, Team.active.is_(True))
        .with_for_update(read=True)
    )
    row = db.get(TeamAIGovernance, team_id, populate_existing=True)
    allowed = team is not None and (
        row is None or provider_key in row.approved_provider_keys
    )
    if row is not None:
        allowed = allowed and all(
            provider_key
            in row.label_destinations.get(str(label), row.approved_provider_keys)
            for label in label_ids
        )
    if not allowed:
        raise AIIntegrationError(
            "This team's current AI destination policy blocks the queued provider. Ask an administrator to review approved destinations, then start a new task.",
            retryable=False,
            provider_io_outcome="not_sent",
            failure_category="team_ai_destination_denied",
        )


def load_team_assessment_settings(
    db: Session, *, team_id: uuid.UUID, item_id: uuid.UUID
) -> "ActiveAISettings":
    """Preview the same route queued work will capture, under caller team fences."""
    from dataclasses import replace
    from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
    from app.models.feed import Feed
    from app.models.item import Item
    from app.services.ai_config import load_active_ai_settings

    key = team_selected_provider(db, team_id)
    active = load_active_ai_settings(
        db,
        feature_type="team_assessment",
        use_legacy=key == "legacy",
        provider_id=uuid.UUID(key[8:]) if key and key.startswith("profile:") else None,
    )
    label = db.scalar(
        select(Feed.handling_label_id)
        .join(Item, Item.feed_id == Feed.id)
        .where(Item.id == item_id)
    )
    try:
        assert_team_ai_destination(
            db,
            team_id=team_id,
            provider_key=f"profile:{active.provider_id}"
            if active.provider_id
            else "legacy",
            label_ids={label or QUARANTINE_HANDLING_LABEL_ID},
        )
    except AIIntegrationError as error:
        return replace(
            active,
            ai_configured=False,
            configuration_error=str(error),
            configuration_error_code="team_ai_destination_denied",
        )
    return active
