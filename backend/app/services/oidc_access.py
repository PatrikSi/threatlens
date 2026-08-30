from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMRole,
    IAMUserRoleAssignment,
)
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.models.oidc import OIDCProvider
from app.models.user import User
from app.services.audit import record_audit
from app.services.authorization import (
    authorization_context_for_user,
    bump_iam_policy_revision,
)
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_permission_reduction,
)
from app.services.oidc_identity import OIDCIdentityError
from app.services.user_access import revoke_user_credentials_with_counts


MAX_OIDC_CLAIM_VALUES = 256
MAX_OIDC_CLAIM_VALUE_LENGTH = 1024
MAX_OIDC_CLAIM_VALUE_BYTES = 64 * 1024
OIDCMappingTarget = tuple[uuid.UUID, str, str, uuid.UUID]


@dataclass(frozen=True)
class OIDCMappingSetDiagnostic:
    mapping_set_id: uuid.UUID
    mapping_set_key: str
    mapping_set_enabled: bool
    claim_present: bool
    claim_value_count: int
    claim_value_fingerprint: str | None
    matched_role_count: int
    matched_group_count: int
    preserved: bool


@dataclass(frozen=True)
class OIDCAccessSyncResult:
    provider_id: uuid.UUID
    policy_id: uuid.UUID | None
    policy_revision: int | None
    policy_generation: int
    enabled: bool
    changed: bool
    added_role_assignments: int = 0
    removed_role_assignments: int = 0
    added_group_memberships: int = 0
    removed_group_memberships: int = 0
    renewed_role_assignments: int = 0
    renewed_group_memberships: int = 0
    reactivated_role_assignments: int = 0
    reactivated_group_memberships: int = 0
    permissions_reduced: bool = False
    revoked_api_tokens: int = 0
    revoked_auth_sessions: int = 0
    cleared_investigation_assignments: int = 0
    diagnostics: tuple[OIDCMappingSetDiagnostic, ...] = ()


@dataclass(frozen=True)
class OIDCAccessPolicySnapshot:
    policy_id: uuid.UUID | None
    revision: int | None
    generation: int


def oidc_access_policy_snapshot(
    db: Session, provider_id: uuid.UUID
) -> OIDCAccessPolicySnapshot:
    row = db.execute(
        select(
            OIDCProvider.oidc_access_policy_generation,
            OIDCAccessPolicy.id,
            OIDCAccessPolicy.revision,
        )
        .outerjoin(
            OIDCAccessPolicy,
            OIDCAccessPolicy.provider_id == OIDCProvider.id,
        )
        .where(OIDCProvider.id == provider_id)
    ).one_or_none()
    if row is None:
        return OIDCAccessPolicySnapshot(policy_id=None, revision=None, generation=0)
    generation, policy_id, revision = row
    return OIDCAccessPolicySnapshot(
        policy_id=policy_id,
        revision=int(revision) if revision is not None else None,
        generation=int(generation or 0),
    )


def oidc_access_policy_matches(
    db: Session,
    provider_id: uuid.UUID,
    *,
    expected_policy_id: uuid.UUID | None,
    expected_revision: int | None,
    expected_generation: int | None,
) -> bool:
    snapshot = oidc_access_policy_snapshot(db, provider_id)
    if expected_generation is None:
        return (
            expected_policy_id is None
            and expected_revision is None
            and snapshot.policy_id is None
        )
    return (
        snapshot.policy_id == expected_policy_id
        and snapshot.revision == expected_revision
        and snapshot.generation == expected_generation
    )


def sync_oidc_access(
    db: Session,
    *,
    provider_id: uuid.UUID,
    user: User,
    claims: Mapping[str, object],
    expected_policy_id: uuid.UUID | None,
    expected_policy_revision: int | None,
    expected_policy_generation: int | None,
    credentials_already_rotated: bool = False,
) -> OIDCAccessSyncResult:
    provider_generation = db.scalar(
        select(OIDCProvider.oidc_access_policy_generation)
        .where(OIDCProvider.id == provider_id)
        .with_for_update(read=True)
    )
    if provider_generation is None:
        raise OIDCIdentityError(
            "provider_configuration_changed",
            "The OIDC provider was removed while sign-in was in progress",
            user_id=str(user.id),
            details={"configuration_component": "access_policy"},
        )
    policy = db.scalar(
        select(OIDCAccessPolicy)
        .where(OIDCAccessPolicy.provider_id == provider_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    current_policy_id = policy.id if policy is not None else None
    current_revision = int(policy.revision) if policy is not None else None
    current_generation = int(provider_generation)
    if (
        current_policy_id != expected_policy_id
        or current_revision != expected_policy_revision
        or (
            expected_policy_generation is not None
            and current_generation != expected_policy_generation
        )
    ):
        raise OIDCIdentityError(
            "provider_configuration_changed",
            "OIDC access mappings changed while sign-in was in progress",
            user_id=str(user.id),
            details={"configuration_component": "access_policy"},
        )
    if policy is None or not policy.enabled:
        return OIDCAccessSyncResult(
            provider_id=provider_id,
            policy_id=current_policy_id,
            policy_revision=current_revision,
            policy_generation=current_generation,
            enabled=False,
            changed=False,
        )

    mapping_sets = list(
        db.scalars(
            select(OIDCClaimMappingSet)
            .where(OIDCClaimMappingSet.access_policy_id == policy.id)
            .order_by(OIDCClaimMappingSet.key, OIDCClaimMappingSet.id)
        ).all()
    )
    if not mapping_sets:
        return OIDCAccessSyncResult(
            provider_id=provider_id,
            policy_id=current_policy_id,
            policy_revision=current_revision,
            policy_generation=current_generation,
            enabled=True,
            changed=False,
        )

    role_mappings, group_mappings = _load_validated_mappings(
        db, mapping_set_ids=[mapping_set.id for mapping_set in mapping_sets]
    )
    desired_roles: dict[str, set[uuid.UUID] | None] = {}
    desired_groups: dict[str, set[uuid.UUID] | None] = {}
    role_mapping_ids: dict[str, uuid.UUID] = {}
    group_mapping_ids: dict[str, uuid.UUID] = {}
    diagnostics: list[OIDCMappingSetDiagnostic] = []
    for mapping_set in mapping_sets:
        configured_role_mappings = role_mappings.get(mapping_set.id, ())
        configured_group_mappings = group_mappings.get(mapping_set.id, ())
        if not mapping_set.enabled:
            desired_roles.update(
                (source_key, set())
                for _mapping_id, source_key, _claim_value, _target_id in configured_role_mappings
            )
            role_mapping_ids.update(
                (source_key, mapping_id)
                for mapping_id, source_key, _claim_value, _target_id in configured_role_mappings
            )
            desired_groups.update(
                (source_key, set())
                for _mapping_id, source_key, _claim_value, _target_id in configured_group_mappings
            )
            group_mapping_ids.update(
                (source_key, mapping_id)
                for mapping_id, source_key, _claim_value, _target_id in configured_group_mappings
            )
            diagnostics.append(
                OIDCMappingSetDiagnostic(
                    mapping_set_id=mapping_set.id,
                    mapping_set_key=mapping_set.key,
                    mapping_set_enabled=False,
                    claim_present=False,
                    claim_value_count=0,
                    claim_value_fingerprint=None,
                    matched_role_count=0,
                    matched_group_count=0,
                    preserved=False,
                )
            )
            continue
        claim_present, claim_values = _mapping_claim_values(
            claims, claim_path=mapping_set.claim_path, mapping_set_key=mapping_set.key
        )
        if not claim_present:
            if mapping_set.missing_claim_behavior == "deny":
                raise OIDCIdentityError(
                    "access_claim_required",
                    "The identity provider did not return a claim required by the configured access policy",
                    user_id=str(user.id),
                    details={"mapping_set_id": str(mapping_set.id)},
                )
            preserve = mapping_set.missing_claim_behavior == "preserve"
            desired_roles.update(
                (source_key, None if preserve else set())
                for _mapping_id, source_key, _claim_value, _target_id in configured_role_mappings
            )
            role_mapping_ids.update(
                (source_key, mapping_id)
                for mapping_id, source_key, _claim_value, _target_id in configured_role_mappings
            )
            desired_groups.update(
                (source_key, None if preserve else set())
                for _mapping_id, source_key, _claim_value, _target_id in configured_group_mappings
            )
            group_mapping_ids.update(
                (source_key, mapping_id)
                for mapping_id, source_key, _claim_value, _target_id in configured_group_mappings
            )
            diagnostics.append(
                OIDCMappingSetDiagnostic(
                    mapping_set_id=mapping_set.id,
                    mapping_set_key=mapping_set.key,
                    mapping_set_enabled=True,
                    claim_present=False,
                    claim_value_count=0,
                    claim_value_fingerprint=None,
                    matched_role_count=0,
                    matched_group_count=0,
                    preserved=preserve,
                )
            )
            continue

        matched_role_ids: set[uuid.UUID] = set()
        matched_group_ids: set[uuid.UUID] = set()
        for mapping_id, source_key, claim_value, role_id in configured_role_mappings:
            role_mapping_ids[source_key] = mapping_id
            matched = claim_value in claim_values
            desired_roles[source_key] = {role_id} if matched else set()
            if matched:
                matched_role_ids.add(role_id)
        for mapping_id, source_key, claim_value, group_id in configured_group_mappings:
            group_mapping_ids[source_key] = mapping_id
            matched = claim_value in claim_values
            desired_groups[source_key] = {group_id} if matched else set()
            if matched:
                matched_group_ids.add(group_id)
        diagnostics.append(
            OIDCMappingSetDiagnostic(
                mapping_set_id=mapping_set.id,
                mapping_set_key=mapping_set.key,
                mapping_set_enabled=True,
                claim_present=True,
                claim_value_count=len(claim_values),
                claim_value_fingerprint=_claim_fingerprint(claim_values),
                matched_role_count=len(matched_role_ids),
                matched_group_count=len(matched_group_ids),
                preserved=False,
            )
        )

    existing_roles = (
        list(
            db.scalars(
                select(IAMUserRoleAssignment).where(
                    IAMUserRoleAssignment.user_id == user.id,
                    IAMUserRoleAssignment.source == "oidc",
                    IAMUserRoleAssignment.source_key.in_(tuple(desired_roles)),
                )
            ).all()
        )
        if desired_roles
        else []
    )
    existing_groups = (
        list(
            db.scalars(
                select(IAMGroupMembership).where(
                    IAMGroupMembership.user_id == user.id,
                    IAMGroupMembership.source == "oidc",
                    IAMGroupMembership.source_key.in_(tuple(desired_groups)),
                )
            ).all()
        )
        if desired_groups
        else []
    )

    before_permissions = authorization_context_for_user(db, user).permissions
    asserted_at = db.scalar(select(func.now()))
    if asserted_at is None:
        raise OIDCIdentityError(
            "access_policy_unavailable",
            "The database clock was unavailable while OIDC access was synchronized",
            user_id=str(user.id),
        )
    grant_ttl_seconds = get_settings().oidc_access_grant_ttl_seconds
    assertion_expires_at = asserted_at + timedelta(seconds=grant_ttl_seconds)
    renew_before = asserted_at + timedelta(seconds=grant_ttl_seconds // 2)
    allow_additions = bool(user.is_active and user.is_approved)
    role_counts = _reconcile_role_assignments(
        db,
        user_id=user.id,
        desired=desired_roles,
        mapping_ids=role_mapping_ids,
        existing=existing_roles,
        asserted_at=asserted_at,
        assertion_expires_at=assertion_expires_at,
        renew_before=renew_before,
        allow_additions=allow_additions,
    )
    group_counts = _reconcile_group_memberships(
        db,
        user_id=user.id,
        desired=desired_groups,
        mapping_ids=group_mapping_ids,
        existing=existing_groups,
        asserted_at=asserted_at,
        assertion_expires_at=assertion_expires_at,
        renew_before=renew_before,
        allow_additions=allow_additions,
    )
    changed = bool(
        role_counts[0]
        or role_counts[1]
        or role_counts[3]
        or group_counts[0]
        or group_counts[1]
        or group_counts[3]
    )
    permissions_reduced = False
    revoked_api_tokens = 0
    revoked_auth_sessions = 0
    cleared_investigation_assignments = 0
    if changed:
        db.flush()
        bump_iam_policy_revision(db)
        after_permissions = authorization_context_for_user(db, user).permissions
        permissions_reduced = bool(before_permissions - after_permissions)
        if (
            "write:investigations" in before_permissions
            and "write:investigations" not in after_permissions
        ):
            try:
                investigation_result = (
                    reconcile_user_investigation_permission_reduction(
                        db,
                        user=user,
                        actor_user_id=user.id,
                    )
                )
            except InvestigationOwnerReassignmentRequired as exc:
                raise OIDCIdentityError(
                    "access_sync_blocked",
                    "OIDC access cannot be reduced until investigation ownership is reassigned",
                    user_id=str(user.id),
                    details={
                        "access_sync_reason": (
                            "investigation_owner_reassignment_required"
                        ),
                        "affected_investigation_count": len(exc.investigations),
                    },
                ) from exc
            cleared_investigation_assignments = (
                investigation_result.cleared_assignment_count
            )
        if permissions_reduced and not credentials_already_rotated:
            revoked = revoke_user_credentials_with_counts(db, user)
            revoked_api_tokens = revoked.api_tokens
            revoked_auth_sessions = revoked.auth_sessions

    return OIDCAccessSyncResult(
        provider_id=provider_id,
        policy_id=current_policy_id,
        policy_revision=current_revision,
        policy_generation=current_generation,
        enabled=True,
        changed=changed,
        added_role_assignments=role_counts[0],
        removed_role_assignments=role_counts[1],
        added_group_memberships=group_counts[0],
        removed_group_memberships=group_counts[1],
        renewed_role_assignments=role_counts[2],
        renewed_group_memberships=group_counts[2],
        reactivated_role_assignments=role_counts[3],
        reactivated_group_memberships=group_counts[3],
        permissions_reduced=permissions_reduced,
        revoked_api_tokens=revoked_api_tokens,
        revoked_auth_sessions=revoked_auth_sessions,
        cleared_investigation_assignments=cleared_investigation_assignments,
        diagnostics=tuple(diagnostics),
    )


def oidc_access_sync_audit_metadata(
    result: OIDCAccessSyncResult,
) -> dict[str, object]:
    return {
        "provider_id": str(result.provider_id),
        "policy_id": str(result.policy_id) if result.policy_id is not None else None,
        "policy_revision": result.policy_revision,
        "policy_generation": result.policy_generation,
        "enabled": result.enabled,
        "changed": result.changed,
        "added_role_assignments": result.added_role_assignments,
        "removed_role_assignments": result.removed_role_assignments,
        "added_group_memberships": result.added_group_memberships,
        "removed_group_memberships": result.removed_group_memberships,
        "renewed_role_assignments": result.renewed_role_assignments,
        "renewed_group_memberships": result.renewed_group_memberships,
        "reactivated_role_assignments": result.reactivated_role_assignments,
        "reactivated_group_memberships": result.reactivated_group_memberships,
        "permissions_reduced": result.permissions_reduced,
        "revoked_api_tokens": result.revoked_api_tokens,
        "revoked_auth_sessions": result.revoked_auth_sessions,
        "cleared_investigation_assignments": (result.cleared_investigation_assignments),
        "mapping_sets": [
            {
                "id": str(diagnostic.mapping_set_id),
                "key": diagnostic.mapping_set_key,
                "enabled": diagnostic.mapping_set_enabled,
                "claim_present": diagnostic.claim_present,
                "claim_value_count": diagnostic.claim_value_count,
                "claim_value_fingerprint": diagnostic.claim_value_fingerprint,
                "matched_role_count": diagnostic.matched_role_count,
                "matched_group_count": diagnostic.matched_group_count,
                "preserved": diagnostic.preserved,
            }
            for diagnostic in result.diagnostics
        ],
    }


def record_oidc_access_sync_audit(
    db: Session,
    *,
    provider: OIDCProvider,
    user: User,
    result: OIDCAccessSyncResult,
) -> None:
    if result.policy_revision is None:
        return
    record_audit(
        db,
        actor_user_id=user.id,
        action="oidc.access.sync",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            **oidc_access_sync_audit_metadata(result),
        },
    )


def _load_validated_mappings(
    db: Session, *, mapping_set_ids: list[uuid.UUID]
) -> tuple[
    dict[uuid.UUID, tuple[OIDCMappingTarget, ...]],
    dict[uuid.UUID, tuple[OIDCMappingTarget, ...]],
]:
    role_rows = db.execute(
        select(OIDCRoleClaimMapping, IAMRole)
        .join(IAMRole, IAMRole.id == OIDCRoleClaimMapping.role_id)
        .where(OIDCRoleClaimMapping.mapping_set_id.in_(mapping_set_ids))
    ).all()
    group_rows = db.execute(
        select(OIDCGroupClaimMapping, IAMGroup)
        .join(IAMGroup, IAMGroup.id == OIDCGroupClaimMapping.group_id)
        .where(OIDCGroupClaimMapping.mapping_set_id.in_(mapping_set_ids))
    ).all()
    invalid_role_count = sum(1 for _mapping, role in role_rows if role.is_system)
    invalid_group_count = sum(1 for _mapping, group in group_rows if group.is_system)
    if invalid_role_count or invalid_group_count:
        raise OIDCIdentityError(
            "access_policy_invalid",
            "OIDC access policy references a protected system role or group",
            details={
                "invalid_role_mapping_count": invalid_role_count,
                "invalid_group_mapping_count": invalid_group_count,
            },
        )

    role_mappings_buffer: dict[uuid.UUID, list[OIDCMappingTarget]] = {}
    for mapping, role in role_rows:
        role_mappings_buffer.setdefault(mapping.mapping_set_id, []).append(
            (mapping.id, mapping.source_key, mapping.claim_value, role.id)
        )
    group_mappings_buffer: dict[uuid.UUID, list[OIDCMappingTarget]] = {}
    for mapping, group in group_rows:
        group_mappings_buffer.setdefault(mapping.mapping_set_id, []).append(
            (mapping.id, mapping.source_key, mapping.claim_value, group.id)
        )
    return (
        {key: tuple(value) for key, value in role_mappings_buffer.items()},
        {key: tuple(value) for key, value in group_mappings_buffer.items()},
    )


def _mapping_claim_values(
    claims: Mapping[str, object], *, claim_path: str, mapping_set_key: str
) -> tuple[bool, frozenset[str]]:
    current: object = claims
    for component in claim_path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return False, frozenset()
        current = current[component]

    if isinstance(current, str):
        raw_values = [current]
    elif isinstance(current, list):
        if len(current) > MAX_OIDC_CLAIM_VALUES or not all(
            isinstance(value, str) for value in current
        ):
            raise _invalid_claim_error(mapping_set_key)
        raw_values = current
    else:
        raise _invalid_claim_error(mapping_set_key)

    if any(len(value) > MAX_OIDC_CLAIM_VALUE_LENGTH for value in raw_values):
        raise _invalid_claim_error(mapping_set_key)
    if any(
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in raw_values
    ):
        raise _invalid_claim_error(mapping_set_key)
    encoded_size = sum(len(value.encode("utf-8")) for value in raw_values)
    if encoded_size > MAX_OIDC_CLAIM_VALUE_BYTES:
        raise _invalid_claim_error(mapping_set_key)
    return True, frozenset(raw_values)


def _invalid_claim_error(mapping_set_key: str) -> OIDCIdentityError:
    return OIDCIdentityError(
        "access_claim_invalid",
        "The identity provider returned an invalid value for a configured access claim",
        details={"mapping_set_key": mapping_set_key},
    )


def _claim_fingerprint(values: frozenset[str]) -> str:
    serialized = json.dumps(
        sorted(values), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    secret = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(secret, serialized, hashlib.sha256).hexdigest()[:16]


def _reconcile_role_assignments(
    db: Session,
    *,
    user_id: uuid.UUID,
    desired: dict[str, set[uuid.UUID] | None],
    mapping_ids: dict[str, uuid.UUID],
    existing: list[IAMUserRoleAssignment],
    asserted_at: datetime,
    assertion_expires_at: datetime,
    renew_before: datetime,
    allow_additions: bool,
) -> tuple[int, int, int, int]:
    by_key = {
        (assignment.source_key, assignment.role_id): assignment
        for assignment in existing
    }
    added = 0
    removed = 0
    renewed = 0
    reactivated = 0
    for (source_key, role_id), assignment in by_key.items():
        target_ids = desired[source_key]
        effective_target_ids = target_ids if allow_additions else set()
        if effective_target_ids is not None and role_id not in effective_target_ids:
            db.delete(assignment)
            removed += 1
        elif effective_target_ids is not None:
            expires_at = assignment.oidc_assertion_expires_at
            if (
                assignment.oidc_role_mapping_id != mapping_ids[source_key]
                or expires_at is None
                or expires_at <= renew_before
                or expires_at > assertion_expires_at
            ):
                if expires_at is not None and expires_at <= asserted_at:
                    reactivated += 1
                assignment.oidc_role_mapping_id = mapping_ids[source_key]
                assignment.oidc_assertion_expires_at = assertion_expires_at
                db.add(assignment)
                renewed += 1
    for source_key, target_ids in desired.items():
        if target_ids is None or not allow_additions:
            continue
        for role_id in target_ids:
            if (source_key, role_id) in by_key:
                continue
            db.add(
                IAMUserRoleAssignment(
                    user_id=user_id,
                    role_id=role_id,
                    source="oidc",
                    source_key=source_key,
                    oidc_role_mapping_id=mapping_ids[source_key],
                    oidc_assertion_expires_at=assertion_expires_at,
                    assigned_by_user_id=None,
                )
            )
            added += 1
    return added, removed, renewed, reactivated


def _reconcile_group_memberships(
    db: Session,
    *,
    user_id: uuid.UUID,
    desired: dict[str, set[uuid.UUID] | None],
    mapping_ids: dict[str, uuid.UUID],
    existing: list[IAMGroupMembership],
    asserted_at: datetime,
    assertion_expires_at: datetime,
    renew_before: datetime,
    allow_additions: bool,
) -> tuple[int, int, int, int]:
    by_key = {
        (membership.source_key, membership.group_id): membership
        for membership in existing
    }
    added = 0
    removed = 0
    renewed = 0
    reactivated = 0
    for (source_key, group_id), membership in by_key.items():
        target_ids = desired[source_key]
        effective_target_ids = target_ids if allow_additions else set()
        if effective_target_ids is not None and group_id not in effective_target_ids:
            db.delete(membership)
            removed += 1
        elif effective_target_ids is not None:
            expires_at = membership.oidc_assertion_expires_at
            if (
                membership.oidc_group_mapping_id != mapping_ids[source_key]
                or expires_at is None
                or expires_at <= renew_before
                or expires_at > assertion_expires_at
            ):
                if expires_at is not None and expires_at <= asserted_at:
                    reactivated += 1
                membership.oidc_group_mapping_id = mapping_ids[source_key]
                membership.oidc_assertion_expires_at = assertion_expires_at
                db.add(membership)
                renewed += 1
    for source_key, target_ids in desired.items():
        if target_ids is None or not allow_additions:
            continue
        for group_id in target_ids:
            if (source_key, group_id) in by_key:
                continue
            db.add(
                IAMGroupMembership(
                    user_id=user_id,
                    group_id=group_id,
                    source="oidc",
                    source_key=source_key,
                    oidc_group_mapping_id=mapping_ids[source_key],
                    oidc_assertion_expires_at=assertion_expires_at,
                    assigned_by_user_id=None,
                )
            )
            added += 1
    return added, removed, renewed, reactivated


__all__ = [
    "MAX_OIDC_CLAIM_VALUE_BYTES",
    "MAX_OIDC_CLAIM_VALUE_LENGTH",
    "MAX_OIDC_CLAIM_VALUES",
    "OIDCAccessPolicySnapshot",
    "OIDCAccessSyncResult",
    "oidc_access_policy_matches",
    "oidc_access_policy_snapshot",
    "oidc_access_sync_audit_metadata",
    "record_oidc_access_sync_audit",
    "sync_oidc_access",
]
