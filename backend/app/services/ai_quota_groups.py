"""Quota administration shares short admission locks, never provider I/O locks."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.ai_provider import AIProviderConfiguration
from app.models.ai_provider_budget import AIProviderBudgetState
from app.models.ai_quota_group import AIQuotaGroup, AIQuotaGroupMember
from app.schemas.ai_quota_groups import (
    AIQuotaGroupCreate,
    AIQuotaGroupFields,
    AIQuotaGroupResponse,
    AIQuotaGroupUpdate,
)
from app.services.ai_providers import AIProviderError, require_provider_version

QUOTA_CONFIGURATION_KEY = "!quota-configuration"


def lock_quota_configuration(db: Session, *, write: bool = False) -> None:
    db.execute(
        insert(AIProviderBudgetState)
        .values(provider_key=QUOTA_CONFIGURATION_KEY)
        .on_conflict_do_nothing()
    )
    db.scalar(
        select(AIProviderBudgetState)
        .where(
            AIProviderBudgetState.provider_key == QUOTA_CONFIGURATION_KEY,
        )
        .with_for_update(read=not write)
    )


def quota_response(db: Session, group: AIQuotaGroup) -> AIQuotaGroupResponse:
    return quota_responses(db, [group])[0]


def quota_responses(
    db: Session, groups: Sequence[AIQuotaGroup]
) -> list[AIQuotaGroupResponse]:
    members: dict[uuid.UUID, list[str]] = {group.id: [] for group in groups}
    if members:
        for group_id, key in db.execute(
            select(AIQuotaGroupMember.group_id, AIQuotaGroupMember.provider_key)
            .where(AIQuotaGroupMember.group_id.in_(members))
            .order_by(AIQuotaGroupMember.provider_key)
        ):
            members[group_id].append(key)
    return [
        AIQuotaGroupResponse(
            id=group.id,
            version=group.version,
            name=group.name,
            provider_keys=members[group.id],
            max_concurrent_requests=group.max_concurrent_requests,
            hourly_token_budget=group.hourly_token_budget,
            max_concurrent_per_team=group.max_concurrent_per_team,
        )
        for group in groups
    ]


def save_quota_group(
    db: Session,
    payload: AIQuotaGroupCreate | AIQuotaGroupUpdate,
    *,
    group_id: uuid.UUID,
) -> AIQuotaGroup:
    lock_quota_configuration(db, write=True)
    group = db.get(AIQuotaGroup, group_id, populate_existing=True)
    creating = isinstance(payload, AIQuotaGroupCreate)
    if creating and group is not None:
        current = quota_response(db, group)
        if group.version == 1 and current.model_dump(
            exclude={"id", "version"}
        ) == payload.model_dump(exclude={"id"}):
            return group
        raise AIProviderError(
            "quota_group_id_conflict",
            "This request ID already belongs to a saved quota group. Reload it before retrying.",
        )
    if not creating:
        if group is None:
            raise AIProviderError(
                "quota_group_not_found",
                "Quota group not found. Reload AI settings.",
                status_code=404,
            )
        require_provider_version(group.version, payload.version)
    elif (db.scalar(select(func.count()).select_from(AIQuotaGroup)) or 0) >= 100:
        raise AIProviderError(
            "quota_group_capacity",
            "At most 100 quota groups are supported. Reuse an existing group.",
            status_code=429,
        )
    if db.scalar(
        select(AIQuotaGroup.id).where(
            AIQuotaGroup.normalized_name == payload.name.casefold(),
            AIQuotaGroup.id != group_id,
        )
    ):
        raise AIProviderError(
            "quota_group_name_conflict", "A quota group already has this name."
        )
    provider_ids = [
        uuid.UUID(key[8:]) for key in payload.provider_keys if key != "legacy"
    ]
    known = set(
        db.scalars(
            select(AIProviderConfiguration.id).where(
                AIProviderConfiguration.id.in_(provider_ids)
            )
        )
    )
    if known != set(provider_ids):
        raise AIProviderError(
            "quota_provider_missing",
            "A selected provider no longer exists. Reload the provider list.",
        )
    assigned = db.scalar(
        select(AIQuotaGroupMember.provider_key)
        .where(
            AIQuotaGroupMember.provider_key.in_(payload.provider_keys),
            AIQuotaGroupMember.group_id != group_id,
        )
        .limit(1)
    )
    if assigned:
        raise AIProviderError(
            "quota_provider_assigned",
            "A selected provider already belongs to another quota group. Remove that membership first.",
        )
    if group is None:
        group = AIQuotaGroup(id=group_id, version=1)
        db.add(group)
    else:
        group.version += 1
    for name in AIQuotaGroupFields.model_fields:
        if name != "provider_keys":
            setattr(group, name, getattr(payload, name))
    group.normalized_name = group.name.casefold()
    db.flush()
    db.execute(
        delete(AIQuotaGroupMember).where(AIQuotaGroupMember.group_id == group_id)
    )
    db.add_all(
        AIQuotaGroupMember(group_id=group_id, provider_key=key)
        for key in payload.provider_keys
    )
    db.flush()
    return group
