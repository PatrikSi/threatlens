from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.iam import IAMGroup, IAMRole
from app.models.oidc import OIDCProvider
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.schemas.oidc_access import (
    OIDCAccessPolicyCreateRequest,
    OIDCAccessPolicyResponse,
    OIDCAccessPolicyStateResponse,
    OIDCAccessPolicyUpdateRequest,
    OIDCClaimMappingSetCreateRequest,
    OIDCClaimMappingSetResponse,
    OIDCClaimMappingSetUpdateRequest,
    OIDCGroupValueMappingResponse,
    OIDCRoleValueMappingResponse,
)
from app.services.oidc_access_lifecycle import (
    OIDCAccessPurgeBlocked,
    OIDCAccessPurgeResult,
    purge_oidc_access,
)


MAX_OIDC_MAPPING_SETS = 128


class OIDCAccessAdminError(RuntimeError):
    code = "oidc_access_policy_error"
    status_code = 409

    def __init__(
        self,
        detail: str,
        *,
        current_revision: int | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.current_revision = current_revision
        self.context = context or {}


class OIDCAccessPolicyNotFound(OIDCAccessAdminError):
    code = "oidc_access_policy_not_found"
    status_code = 404


class OIDCAccessPolicyConflict(OIDCAccessAdminError):
    code = "oidc_access_policy_conflict"


class OIDCAccessPolicyRevisionConflict(OIDCAccessAdminError):
    code = "oidc_access_policy_revision_conflict"


class OIDCMappingSetNotFound(OIDCAccessAdminError):
    code = "oidc_mapping_set_not_found"
    status_code = 404


class OIDCMappingSetConflict(OIDCAccessAdminError):
    code = "oidc_mapping_set_conflict"


class OIDCMappingSetRevisionConflict(OIDCAccessAdminError):
    code = "oidc_mapping_set_revision_conflict"


class OIDCMappingTargetInvalid(OIDCAccessAdminError):
    code = "oidc_mapping_target_invalid"
    status_code = 422


class OIDCMappingSetLimitExceeded(OIDCAccessAdminError):
    code = "oidc_mapping_set_limit_exceeded"
    status_code = 422


class OIDCAccessOwnershipConflict(OIDCAccessAdminError):
    code = "oidc_access_investigation_owner_reassignment_required"


@dataclass(frozen=True)
class OIDCAccessMutationResult:
    policy: OIDCAccessPolicyResponse | None
    mapping_set: OIDCClaimMappingSetResponse | None
    policy_generation: int
    affected_policy_id: uuid.UUID | None = None
    affected_mapping_set_id: uuid.UUID | None = None
    previous_policy_revision: int | None = None
    previous_mapping_set_revision: int | None = None
    purged_role_assignments: int = 0
    purged_group_memberships: int = 0
    affected_user_count: int = 0
    access_reduced_user_count: int = 0
    revoked_api_tokens: int = 0
    revoked_auth_sessions: int = 0
    cancelled_pending_mfa_enrollments: int = 0
    cleared_investigation_assignments: int = 0
    iam_policy_revision: int | None = None


def access_policy_state(
    db: Session, provider_id: uuid.UUID | None
) -> OIDCAccessPolicyStateResponse:
    if provider_id is None:
        return OIDCAccessPolicyStateResponse(configured=False)
    policy = db.scalar(
        select(OIDCAccessPolicy).where(OIDCAccessPolicy.provider_id == provider_id)
    )
    return OIDCAccessPolicyStateResponse(
        configured=policy is not None,
        provider_id=provider_id,
        policy=access_policy_response(db, policy) if policy is not None else None,
    )


def create_access_policy(
    db: Session,
    *,
    provider_id: uuid.UUID,
    payload: OIDCAccessPolicyCreateRequest,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    existing = db.scalar(
        select(OIDCAccessPolicy)
        .where(OIDCAccessPolicy.provider_id == provider_id)
        .with_for_update()
    )
    if existing is not None:
        raise OIDCAccessPolicyConflict(
            "OIDC custom access policy is already configured. Reload it and update the existing policy.",
            current_revision=existing.revision,
        )
    policy = OIDCAccessPolicy(
        provider_id=provider_id,
        enabled=payload.enabled,
        revision=1,
        updated_by_user_id=actor_user_id,
    )
    db.add(policy)
    generation = _bump_access_policy_generation(db, provider_id)
    _flush_or_conflict(
        db,
        OIDCAccessPolicyConflict(
            "OIDC custom access policy was created concurrently. Reload it and retry."
        ),
    )
    return OIDCAccessMutationResult(
        policy=access_policy_response(db, policy),
        mapping_set=None,
        policy_generation=generation,
    )


def update_access_policy(
    db: Session,
    *,
    provider_id: uuid.UUID,
    payload: OIDCAccessPolicyUpdateRequest,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    policy = _lock_policy(db, provider_id)
    _require_policy_revision(policy, payload.expected_revision)
    purge_result = OIDCAccessPurgeResult()
    if policy.enabled and not payload.enabled:
        source_keys = _policy_source_keys(db, policy.id)
        purge_result = _purge_materialized_access(
            db,
            role_source_keys=source_keys[0],
            group_source_keys=source_keys[1],
            actor_user_id=actor_user_id,
        )
    policy.enabled = payload.enabled
    policy.revision += 1
    policy.updated_by_user_id = actor_user_id
    db.add(policy)
    generation = _bump_access_policy_generation(db, provider_id)
    db.flush()
    db.refresh(policy)
    return OIDCAccessMutationResult(
        policy=access_policy_response(db, policy),
        mapping_set=None,
        policy_generation=generation,
        **_purge_result_fields(purge_result),
    )


def delete_access_policy(
    db: Session,
    *,
    provider_id: uuid.UUID,
    expected_revision: int,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    policy = _lock_policy(db, provider_id)
    _require_policy_revision(policy, expected_revision)
    deleted_policy_id = policy.id
    deleted_policy_revision = int(policy.revision)
    role_source_keys, group_source_keys = _policy_source_keys(db, policy.id)
    purge_result = _purge_materialized_access(
        db,
        role_source_keys=role_source_keys,
        group_source_keys=group_source_keys,
        actor_user_id=actor_user_id,
    )
    db.delete(policy)
    generation = _bump_access_policy_generation(db, provider_id)
    db.flush()
    return OIDCAccessMutationResult(
        policy=None,
        mapping_set=None,
        policy_generation=generation,
        affected_policy_id=deleted_policy_id,
        previous_policy_revision=deleted_policy_revision,
        **_purge_result_fields(purge_result),
    )


def create_mapping_set(
    db: Session,
    *,
    provider_id: uuid.UUID,
    payload: OIDCClaimMappingSetCreateRequest,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    policy = _lock_policy(db, provider_id)
    mapping_set_count = int(
        db.scalar(
            select(func.count(OIDCClaimMappingSet.id)).where(
                OIDCClaimMappingSet.access_policy_id == policy.id
            )
        )
        or 0
    )
    if mapping_set_count >= MAX_OIDC_MAPPING_SETS:
        raise OIDCMappingSetLimitExceeded(
            f"OIDC access policy supports at most {MAX_OIDC_MAPPING_SETS} mapping sets. Remove an obsolete set before adding another."
        )
    _validated_targets(db, payload.role_mappings, payload.group_mappings)
    mapping_set = OIDCClaimMappingSet(
        id=uuid.uuid4(),
        access_policy_id=policy.id,
        key=payload.key,
        name=payload.name,
        claim_path=payload.claim_path,
        missing_claim_behavior=payload.missing_claim_behavior,
        enabled=payload.enabled,
        revision=1,
        updated_by_user_id=actor_user_id,
    )
    db.add(mapping_set)
    _replace_role_mappings(db, mapping_set.id, payload.role_mappings, existing=[])
    _replace_group_mappings(db, mapping_set.id, payload.group_mappings, existing=[])
    policy.revision += 1
    policy.updated_by_user_id = actor_user_id
    db.add(policy)
    generation = _bump_access_policy_generation(db, provider_id)
    _flush_or_conflict(
        db,
        OIDCMappingSetConflict(
            "A mapping set with this key was created concurrently. Reload access policy and retry."
        ),
    )
    db.refresh(mapping_set)
    return OIDCAccessMutationResult(
        policy=access_policy_response(db, policy),
        mapping_set=mapping_set_response(db, mapping_set),
        policy_generation=generation,
    )


def update_mapping_set(
    db: Session,
    *,
    provider_id: uuid.UUID,
    mapping_set_id: uuid.UUID,
    payload: OIDCClaimMappingSetUpdateRequest,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    policy = _lock_policy(db, provider_id)
    mapping_set = _lock_mapping_set(db, policy.id, mapping_set_id)
    _require_mapping_set_revision(mapping_set, payload.expected_revision)
    existing_roles = _role_mappings(db, mapping_set.id)
    existing_groups = _group_mappings(db, mapping_set.id)
    next_roles = payload.role_mappings
    next_groups = payload.group_mappings
    _validated_targets(db, next_roles or [], next_groups or [])

    purge_all = (
        (
            payload.claim_path is not None
            and payload.claim_path != mapping_set.claim_path
        )
        or (
            payload.missing_claim_behavior is not None
            and payload.missing_claim_behavior != mapping_set.missing_claim_behavior
        )
        or (payload.enabled is False and mapping_set.enabled)
    )
    purged_role_keys: set[str] = (
        {mapping.source_key for mapping in existing_roles} if purge_all else set()
    )
    purged_group_keys: set[str] = (
        {mapping.source_key for mapping in existing_groups} if purge_all else set()
    )
    if next_roles is not None:
        purged_role_keys.update(_changed_role_source_keys(existing_roles, next_roles))
    if next_groups is not None:
        purged_group_keys.update(
            _changed_group_source_keys(existing_groups, next_groups)
        )
    purge_result = _purge_materialized_access(
        db,
        role_source_keys=purged_role_keys,
        group_source_keys=purged_group_keys,
        actor_user_id=actor_user_id,
    )

    if payload.name is not None:
        mapping_set.name = payload.name
    if payload.claim_path is not None:
        mapping_set.claim_path = payload.claim_path
    if payload.missing_claim_behavior is not None:
        mapping_set.missing_claim_behavior = payload.missing_claim_behavior
    if payload.enabled is not None:
        mapping_set.enabled = payload.enabled
    if next_roles is not None:
        _replace_role_mappings(db, mapping_set.id, next_roles, existing=existing_roles)
    if next_groups is not None:
        _replace_group_mappings(
            db, mapping_set.id, next_groups, existing=existing_groups
        )
    mapping_set.revision += 1
    mapping_set.updated_by_user_id = actor_user_id
    policy.revision += 1
    policy.updated_by_user_id = actor_user_id
    db.add_all([mapping_set, policy])
    generation = _bump_access_policy_generation(db, provider_id)
    _flush_or_conflict(
        db,
        OIDCMappingSetConflict(
            "OIDC mapping targets changed concurrently. Reload access policy and retry."
        ),
    )
    db.refresh(mapping_set)
    return OIDCAccessMutationResult(
        policy=access_policy_response(db, policy),
        mapping_set=mapping_set_response(db, mapping_set),
        policy_generation=generation,
        **_purge_result_fields(purge_result),
    )


def delete_mapping_set(
    db: Session,
    *,
    provider_id: uuid.UUID,
    mapping_set_id: uuid.UUID,
    expected_revision: int,
    actor_user_id: uuid.UUID,
) -> OIDCAccessMutationResult:
    policy = _lock_policy(db, provider_id)
    mapping_set = _lock_mapping_set(db, policy.id, mapping_set_id)
    _require_mapping_set_revision(mapping_set, expected_revision)
    deleted_mapping_set_id = mapping_set.id
    deleted_mapping_set_revision = int(mapping_set.revision)
    role_source_keys = {
        mapping.source_key for mapping in _role_mappings(db, mapping_set.id)
    }
    group_source_keys = {
        mapping.source_key for mapping in _group_mappings(db, mapping_set.id)
    }
    purge_result = _purge_materialized_access(
        db,
        role_source_keys=role_source_keys,
        group_source_keys=group_source_keys,
        actor_user_id=actor_user_id,
    )
    db.delete(mapping_set)
    policy.revision += 1
    policy.updated_by_user_id = actor_user_id
    db.add(policy)
    generation = _bump_access_policy_generation(db, provider_id)
    db.flush()
    return OIDCAccessMutationResult(
        policy=access_policy_response(db, policy),
        mapping_set=None,
        policy_generation=generation,
        affected_policy_id=policy.id,
        affected_mapping_set_id=deleted_mapping_set_id,
        previous_policy_revision=int(policy.revision) - 1,
        previous_mapping_set_revision=deleted_mapping_set_revision,
        **_purge_result_fields(purge_result),
    )


def access_policy_response(
    db: Session, policy: OIDCAccessPolicy
) -> OIDCAccessPolicyResponse:
    mapping_sets = list(
        db.scalars(
            select(OIDCClaimMappingSet)
            .where(OIDCClaimMappingSet.access_policy_id == policy.id)
            .order_by(OIDCClaimMappingSet.name, OIDCClaimMappingSet.id)
        ).all()
    )
    generation = int(
        db.scalar(
            select(OIDCProvider.oidc_access_policy_generation).where(
                OIDCProvider.id == policy.provider_id
            )
        )
        or 0
    )
    return OIDCAccessPolicyResponse(
        id=policy.id,
        provider_id=policy.provider_id,
        enabled=policy.enabled,
        revision=policy.revision,
        generation=generation,
        mapping_sets=[mapping_set_response(db, row) for row in mapping_sets],
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def mapping_set_response(
    db: Session, mapping_set: OIDCClaimMappingSet
) -> OIDCClaimMappingSetResponse:
    roles = _role_mappings(db, mapping_set.id)
    groups = _group_mappings(db, mapping_set.id)
    return OIDCClaimMappingSetResponse(
        id=mapping_set.id,
        access_policy_id=mapping_set.access_policy_id,
        key=mapping_set.key,
        name=mapping_set.name,
        claim_path=mapping_set.claim_path,
        missing_claim_behavior=mapping_set.missing_claim_behavior,
        enabled=mapping_set.enabled,
        revision=mapping_set.revision,
        role_mappings=[
            OIDCRoleValueMappingResponse.model_validate(row) for row in roles
        ],
        group_mappings=[
            OIDCGroupValueMappingResponse.model_validate(row) for row in groups
        ],
        created_at=mapping_set.created_at,
        updated_at=mapping_set.updated_at,
    )


def _lock_policy(db: Session, provider_id: uuid.UUID) -> OIDCAccessPolicy:
    policy = db.scalar(
        select(OIDCAccessPolicy)
        .where(OIDCAccessPolicy.provider_id == provider_id)
        .with_for_update()
    )
    if policy is None:
        raise OIDCAccessPolicyNotFound(
            "OIDC custom access policy is not configured. Create it before adding mappings."
        )
    return policy


def _lock_mapping_set(
    db: Session, policy_id: uuid.UUID, mapping_set_id: uuid.UUID
) -> OIDCClaimMappingSet:
    mapping_set = db.scalar(
        select(OIDCClaimMappingSet)
        .where(
            OIDCClaimMappingSet.id == mapping_set_id,
            OIDCClaimMappingSet.access_policy_id == policy_id,
        )
        .with_for_update()
    )
    if mapping_set is None:
        raise OIDCMappingSetNotFound("OIDC claim mapping set was not found.")
    return mapping_set


def _require_policy_revision(policy: OIDCAccessPolicy, expected_revision: int) -> None:
    if policy.revision != expected_revision:
        raise OIDCAccessPolicyRevisionConflict(
            "OIDC custom access policy changed after it was loaded. Reload it and retry.",
            current_revision=policy.revision,
            context={
                "expected_revision": expected_revision,
                "current_revision": policy.revision,
            },
        )


def _require_mapping_set_revision(
    mapping_set: OIDCClaimMappingSet, expected_revision: int
) -> None:
    if mapping_set.revision != expected_revision:
        raise OIDCMappingSetRevisionConflict(
            "OIDC claim mapping set changed after it was loaded. Reload access policy and retry.",
            current_revision=mapping_set.revision,
            context={
                "mapping_set_id": str(mapping_set.id),
                "expected_revision": expected_revision,
                "current_revision": mapping_set.revision,
            },
        )


def _validated_targets(db: Session, role_mappings, group_mappings) -> None:
    role_ids = {mapping.role_id for mapping in role_mappings}
    group_ids = {mapping.group_id for mapping in group_mappings}
    valid_role_ids = (
        set(
            db.scalars(
                select(IAMRole.id).where(
                    IAMRole.id.in_(role_ids), IAMRole.is_system.is_(False)
                )
            ).all()
        )
        if role_ids
        else set()
    )
    valid_group_ids = (
        set(
            db.scalars(
                select(IAMGroup.id).where(
                    IAMGroup.id.in_(group_ids), IAMGroup.is_system.is_(False)
                )
            ).all()
        )
        if group_ids
        else set()
    )
    missing_roles = sorted(str(role_id) for role_id in role_ids - valid_role_ids)
    missing_groups = sorted(str(group_id) for group_id in group_ids - valid_group_ids)
    if missing_roles or missing_groups:
        raise OIDCMappingTargetInvalid(
            "OIDC mappings may reference only existing custom roles and non-system groups.",
            context={
                "invalid_role_ids": missing_roles,
                "invalid_group_ids": missing_groups,
            },
        )


def _role_mappings(
    db: Session, mapping_set_id: uuid.UUID
) -> list[OIDCRoleClaimMapping]:
    return list(
        db.scalars(
            select(OIDCRoleClaimMapping)
            .where(OIDCRoleClaimMapping.mapping_set_id == mapping_set_id)
            .order_by(OIDCRoleClaimMapping.claim_value, OIDCRoleClaimMapping.id)
        ).all()
    )


def _group_mappings(
    db: Session, mapping_set_id: uuid.UUID
) -> list[OIDCGroupClaimMapping]:
    return list(
        db.scalars(
            select(OIDCGroupClaimMapping)
            .where(OIDCGroupClaimMapping.mapping_set_id == mapping_set_id)
            .order_by(OIDCGroupClaimMapping.claim_value, OIDCGroupClaimMapping.id)
        ).all()
    )


def _replace_role_mappings(
    db: Session, mapping_set_id: uuid.UUID, payloads, *, existing
) -> None:
    existing_by_value = {mapping.claim_value: mapping for mapping in existing}
    desired_values = {payload.claim_value for payload in payloads}
    for claim_value, mapping in existing_by_value.items():
        if claim_value not in desired_values:
            db.delete(mapping)
    for payload in payloads:
        mapping = existing_by_value.get(payload.claim_value)
        if mapping is None:
            db.add(
                OIDCRoleClaimMapping(
                    mapping_set_id=mapping_set_id,
                    claim_value=payload.claim_value,
                    role_id=payload.role_id,
                    role_is_system=False,
                )
            )
        elif mapping.role_id != payload.role_id:
            mapping.role_id = payload.role_id
            db.add(mapping)


def _replace_group_mappings(
    db: Session, mapping_set_id: uuid.UUID, payloads, *, existing
) -> None:
    existing_by_value = {mapping.claim_value: mapping for mapping in existing}
    desired_values = {payload.claim_value for payload in payloads}
    for claim_value, mapping in existing_by_value.items():
        if claim_value not in desired_values:
            db.delete(mapping)
    for payload in payloads:
        mapping = existing_by_value.get(payload.claim_value)
        if mapping is None:
            db.add(
                OIDCGroupClaimMapping(
                    mapping_set_id=mapping_set_id,
                    claim_value=payload.claim_value,
                    group_id=payload.group_id,
                    group_is_system=False,
                )
            )
        elif mapping.group_id != payload.group_id:
            mapping.group_id = payload.group_id
            db.add(mapping)


def _changed_role_source_keys(existing, payloads) -> set[str]:
    desired = {payload.claim_value: payload.role_id for payload in payloads}
    return {
        mapping.source_key
        for mapping in existing
        if desired.get(mapping.claim_value) != mapping.role_id
    }


def _changed_group_source_keys(existing, payloads) -> set[str]:
    desired = {payload.claim_value: payload.group_id for payload in payloads}
    return {
        mapping.source_key
        for mapping in existing
        if desired.get(mapping.claim_value) != mapping.group_id
    }


def _policy_source_keys(db: Session, policy_id: uuid.UUID) -> tuple[set[str], set[str]]:
    mapping_set_ids = select(OIDCClaimMappingSet.id).where(
        OIDCClaimMappingSet.access_policy_id == policy_id
    )
    role_keys = set(
        db.scalars(
            select(OIDCRoleClaimMapping.source_key).where(
                OIDCRoleClaimMapping.mapping_set_id.in_(mapping_set_ids)
            )
        ).all()
    )
    group_keys = set(
        db.scalars(
            select(OIDCGroupClaimMapping.source_key).where(
                OIDCGroupClaimMapping.mapping_set_id.in_(mapping_set_ids)
            )
        ).all()
    )
    return role_keys, group_keys


def _purge_materialized_access(
    db: Session,
    *,
    role_source_keys: set[str],
    group_source_keys: set[str],
    actor_user_id: uuid.UUID,
) -> OIDCAccessPurgeResult:
    try:
        return purge_oidc_access(
            db,
            role_source_keys=role_source_keys,
            group_source_keys=group_source_keys,
            actor_user_id=actor_user_id,
            revocation_reason="oidc_access_policy_changed",
        )
    except OIDCAccessPurgeBlocked as exc:
        raise OIDCAccessOwnershipConflict(
            str(exc),
            context={
                "user_id": str(exc.user_id),
                "affected_investigation_count": len(exc.investigations),
            },
        ) from exc


def _purge_result_fields(result: OIDCAccessPurgeResult) -> dict[str, int | None]:
    return {
        "purged_role_assignments": result.removed_role_assignments,
        "purged_group_memberships": result.removed_group_memberships,
        "affected_user_count": result.affected_user_count,
        "access_reduced_user_count": result.access_reduced_user_count,
        "revoked_api_tokens": result.revoked_api_tokens,
        "revoked_auth_sessions": result.revoked_auth_sessions,
        "cancelled_pending_mfa_enrollments": (result.cancelled_pending_mfa_enrollments),
        "cleared_investigation_assignments": (result.cleared_investigation_assignments),
        "iam_policy_revision": result.iam_policy_revision,
    }


def _bump_access_policy_generation(db: Session, provider_id: uuid.UUID) -> int:
    provider = db.scalar(
        select(OIDCProvider).where(OIDCProvider.id == provider_id).with_for_update()
    )
    if provider is None:
        raise OIDCAccessPolicyConflict(
            "OIDC provider disappeared while access policy was being changed. Reload settings and retry."
        )
    provider.oidc_access_policy_generation = (
        int(provider.oidc_access_policy_generation or 0) + 1
    )
    db.add(provider)
    db.flush()
    return provider.oidc_access_policy_generation


def _flush_or_conflict(db: Session, error: OIDCAccessAdminError) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise error from exc


__all__ = [
    "OIDCAccessAdminError",
    "OIDCAccessMutationResult",
    "access_policy_state",
    "create_access_policy",
    "create_mapping_set",
    "delete_access_policy",
    "delete_mapping_set",
    "update_access_policy",
    "update_mapping_set",
]
