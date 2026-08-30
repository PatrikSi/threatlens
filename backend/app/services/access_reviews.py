from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.access_review import (
    ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES,
    AccessReviewAssignmentSnapshot as AssignmentSnapshot,
    AccessReviewApplyReceipt,
    AccessReviewCampaign,
    AccessReviewDecision,
    AccessReviewItem,
    access_review_item_from_snapshot as _item_from_snapshot,
    access_review_snapshot_digest as _digest,
    access_review_snapshot_datetime as _iso,
    access_review_snapshot_json,
    access_review_snapshot_uuid as _uuid_text,
    build_access_review_assignment_snapshot as _build_snapshot,
)
from app.models.iam import (
    IAMGroup,
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.oidc import OIDCProvider
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.models.service_account import ServiceAccount, ServiceAccountRoleAssignment
from app.models.temporary_elevation import (
    TemporaryElevation,
    TemporaryElevationPermission,
)
from app.models.user import User
from app.schemas.access_review import (
    AccessReviewBeginApplyRequest,
    AccessReviewCampaignCreate,
    AccessReviewDecisionBatchRequest,
    AccessReviewTransitionRequest,
)
from app.services.authorization import database_clock, lock_iam_policy_for_mutation


MAX_ACCESS_REVIEW_ITEMS = 10_000
MAX_SNAPSHOT_JSON_BYTES = 65_536
MAX_PERMISSION_JSON_BYTES = 32_768


class AccessReviewError(RuntimeError):
    code = "access_review_error"


class AccessReviewNotFound(AccessReviewError):
    code = "access_review_not_found"


class AccessReviewConflict(AccessReviewError):
    code = "access_review_conflict"


class AccessReviewForbidden(AccessReviewError):
    code = "access_review_forbidden"


class AccessReviewRevisionConflict(AccessReviewConflict):
    code = "access_review_revision_conflict"

    def __init__(self, campaign: AccessReviewCampaign) -> None:
        self.current_revision = campaign.revision
        super().__init__(
            "This access-review campaign changed after it was loaded. Reload it and retry."
        )


class AccessReviewStateConflict(AccessReviewConflict):
    code = "access_review_state_conflict"


class AccessReviewSelectionInvalid(AccessReviewConflict):
    code = "access_review_selection_invalid"


class AccessReviewLimitExceeded(AccessReviewConflict):
    code = "access_review_limit_exceeded"


class AccessReviewIncomplete(AccessReviewConflict):
    code = "access_review_incomplete"


class AccessReviewApplyCoordinatorMissing(AccessReviewConflict):
    code = "access_review_apply_coordinator_missing"


def create_access_review_campaign(
    db: Session,
    *,
    creator: User,
    payload: AccessReviewCampaignCreate,
) -> AccessReviewCampaign:
    # Capture one serialized IAM policy boundary without changing it.
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    _require_selected_principals(db, payload)
    estimated_item_count = _count_assignment_snapshots(db, payload=payload, now=now)
    if estimated_item_count > MAX_ACCESS_REVIEW_ITEMS:
        raise AccessReviewLimitExceeded(
            f"The selected scope contains at least {estimated_item_count:,} assignments; a campaign can contain at most {MAX_ACCESS_REVIEW_ITEMS:,}. Narrow the principal selection."
        )
    snapshots = _collect_assignment_snapshots(db, payload=payload, now=now)
    if not snapshots:
        raise AccessReviewSelectionInvalid(
            "The selected scope has no reviewable access assignments. Choose principals with access or include OIDC mappings."
        )
    if len(snapshots) > MAX_ACCESS_REVIEW_ITEMS:
        raise AccessReviewLimitExceeded(
            f"The selected scope contains {len(snapshots):,} assignments; a campaign can contain at most {MAX_ACCESS_REVIEW_ITEMS:,}. Narrow the principal selection."
        )

    scope_snapshot = payload.scope_snapshot()
    _require_json_size(scope_snapshot, "Campaign scope")
    campaign = AccessReviewCampaign(
        name=payload.name,
        description=payload.description,
        scope_snapshot=scope_snapshot,
        scope_digest=_digest(scope_snapshot),
        snapshot_at=now,
        review_due_at=now + timedelta(seconds=payload.due_in_seconds),
        item_count=len(snapshots),
        created_by_user_id=creator.id,
        created_by_email_snapshot=creator.email,
        status="open",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(campaign)
    db.flush()

    ordered = sorted(
        snapshots,
        key=lambda value: (
            value.item_type,
            str(value.principal_id),
            str(value.assignment_id),
        ),
    )
    db.add_all(
        _item_from_snapshot(campaign.id, ordinal, snapshot, now)
        for ordinal, snapshot in enumerate(ordered, start=1)
    )
    db.flush()
    return campaign


def record_access_review_decisions(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    reviewer: User,
    payload: AccessReviewDecisionBatchRequest,
) -> list[AccessReviewDecision]:
    campaign = _lock_campaign(db, campaign_id)
    _require_revision(campaign, payload.expected_revision)
    if campaign.status != "open":
        raise AccessReviewStateConflict(
            f"Decisions can be recorded only while a campaign is open; this campaign is {campaign.status}."
        )

    requested_ids = sorted(
        (decision.item_id for decision in payload.decisions), key=str
    )
    items = list(
        db.scalars(
            select(AccessReviewItem)
            .where(
                AccessReviewItem.campaign_id == campaign.id,
                AccessReviewItem.id.in_(requested_ids),
            )
            .order_by(AccessReviewItem.id)
            .with_for_update()
        ).all()
    )
    item_by_id = {item.id: item for item in items}
    missing = [item_id for item_id in requested_ids if item_id not in item_by_id]
    if missing:
        raise AccessReviewNotFound(
            "One or more decision items do not belong to this campaign. Reload the campaign and retry."
        )
    require_independent_access_review_actor(
        db,
        items=items,
        actor_id=reviewer.id,
        operation_label="decide",
    )

    latest_sequences = dict(
        db.execute(
            select(
                AccessReviewDecision.item_id,
                func.max(AccessReviewDecision.sequence),
            )
            .where(AccessReviewDecision.item_id.in_(requested_ids))
            .group_by(AccessReviewDecision.item_id)
        ).all()
    )
    now = _database_now(db)
    input_by_id = {decision.item_id: decision for decision in payload.decisions}
    rows: list[AccessReviewDecision] = []
    for item_id in requested_ids:
        item = item_by_id[item_id]
        decision_input = input_by_id[item_id]
        row = AccessReviewDecision(
            campaign_id=campaign.id,
            item_id=item.id,
            item_fingerprint=item.assignment_fingerprint,
            sequence=int(latest_sequences.get(item.id) or 0) + 1,
            decision=decision_input.decision,
            decided_by_user_id=reviewer.id,
            decided_by_email_snapshot=reviewer.email,
            reason=decision_input.reason,
            decided_at=now,
        )
        db.add(row)
        rows.append(row)

    campaign.revision += 1
    campaign.updated_at = now
    db.add(campaign)
    db.flush()
    return rows


def require_independent_access_review_actor(
    db: Session,
    *,
    items: Sequence[AccessReviewItem],
    actor_id: uuid.UUID,
    operation_label: str,
) -> None:
    affected_item_ids = {
        item.id
        for item in items
        if item.principal_type == "user" and item.principal_id_snapshot == actor_id
    }
    oidc_role_items = {
        item.assignment_id: item.id
        for item in items
        if item.item_type == "oidc_role_mapping"
    }
    if oidc_role_items:
        mapping_ids = set(
            db.scalars(
                select(IAMUserRoleAssignment.oidc_role_mapping_id).where(
                    IAMUserRoleAssignment.user_id == actor_id,
                    IAMUserRoleAssignment.oidc_role_mapping_id.in_(oidc_role_items),
                )
            ).all()
        )
        affected_item_ids.update(
            oidc_role_items[mapping_id]
            for mapping_id in mapping_ids
            if mapping_id is not None
        )
    oidc_group_items = {
        item.assignment_id: item.id
        for item in items
        if item.item_type == "oidc_group_mapping"
    }
    if oidc_group_items:
        mapping_ids = set(
            db.scalars(
                select(IAMGroupMembership.oidc_group_mapping_id).where(
                    IAMGroupMembership.user_id == actor_id,
                    IAMGroupMembership.oidc_group_mapping_id.in_(oidc_group_items),
                )
            ).all()
        )
        affected_item_ids.update(
            oidc_group_items[mapping_id]
            for mapping_id in mapping_ids
            if mapping_id is not None
        )
    if affected_item_ids:
        raise AccessReviewForbidden(
            f"Reviewers cannot {operation_label} access that applies to their own account. Assign the affected item to another reviewer."
        )


def close_access_review_campaign(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    actor: User,
    payload: AccessReviewTransitionRequest,
) -> AccessReviewCampaign:
    campaign = _lock_campaign(db, campaign_id)
    _require_revision(campaign, payload.expected_revision)
    if campaign.status != "open":
        raise AccessReviewStateConflict(
            f"Only an open campaign can be closed; this campaign is {campaign.status}."
        )
    decided_count = _decision_count(db, campaign.id)
    if decided_count != campaign.item_count:
        raise AccessReviewIncomplete(
            f"The campaign has decisions for {decided_count} of {campaign.item_count} items. Decide every item before closing it."
        )
    now = _database_now(db)
    campaign.status = "closed"
    campaign.closed_by_user_id = actor.id
    campaign.closed_by_email_snapshot = actor.email
    campaign.closed_at = now
    campaign.close_reason = payload.reason
    _advance_campaign(campaign, now)
    db.flush()
    return campaign


def begin_access_review_apply(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    actor: User,
    payload: AccessReviewBeginApplyRequest,
) -> AccessReviewCampaign:
    campaign = _lock_campaign(db, campaign_id)
    _require_revision(campaign, payload.expected_revision)
    if campaign.status != "closed":
        raise AccessReviewStateConflict(
            f"Only a closed campaign can enter apply; this campaign is {campaign.status}."
        )
    now = _database_now(db)
    campaign.status = "applying"
    campaign.apply_started_by_user_id = actor.id
    campaign.apply_started_by_email_snapshot = actor.email
    campaign.apply_started_at = now
    campaign.apply_run_id = uuid.uuid4()
    _advance_campaign(campaign, now)
    db.flush()
    return campaign


def complete_access_review_apply(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    actor: User,
    expected_revision: int,
) -> AccessReviewCampaign:
    campaign = _lock_campaign(db, campaign_id)
    _require_revision(campaign, expected_revision)
    if campaign.status != "applying":
        raise AccessReviewStateConflict(
            f"Only an applying campaign can be completed; this campaign is {campaign.status}."
        )
    terminal_count = _terminal_apply_count(db, campaign.id)
    if terminal_count != campaign.item_count:
        raise AccessReviewIncomplete(
            f"Only {terminal_count} of {campaign.item_count} items have terminal apply receipts. Resolve drifted, failed, or unprocessed items first."
        )
    now = _database_now(db)
    campaign.status = "applied"
    campaign.applied_by_user_id = actor.id
    campaign.applied_by_email_snapshot = actor.email
    campaign.applied_at = now
    _advance_campaign(campaign, now)
    db.flush()
    return campaign


def cancel_access_review_campaign(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    actor: User,
    payload: AccessReviewTransitionRequest,
) -> AccessReviewCampaign:
    campaign = _lock_campaign(db, campaign_id)
    _require_revision(campaign, payload.expected_revision)
    if campaign.status != "open":
        raise AccessReviewStateConflict(
            f"Only an open campaign can be cancelled; this campaign is {campaign.status}."
        )
    now = _database_now(db)
    campaign.status = "cancelled"
    campaign.cancelled_by_user_id = actor.id
    campaign.cancelled_by_principal_type = "user"
    campaign.cancelled_by_email_snapshot = actor.email
    campaign.cancelled_at = now
    campaign.cancel_reason = payload.reason
    _advance_campaign(campaign, now)
    db.flush()
    return campaign


def quarantine_access_review_campaign(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    reason: str,
    actor: User | None = None,
) -> AccessReviewCampaign:
    campaign = _lock_campaign(db, campaign_id)
    if campaign.status not in {"open", "closed", "applying"}:
        raise AccessReviewStateConflict(
            f"A {campaign.status} campaign cannot be quarantined."
        )
    normalized_reason = reason.strip()
    if not 3 <= len(normalized_reason) <= 2_000:
        raise AccessReviewConflict(
            "A quarantine reason between 3 and 2,000 characters is required."
        )
    now = _database_now(db)
    campaign.status = "quarantined"
    campaign.quarantined_by_user_id = actor.id if actor is not None else None
    campaign.quarantined_by_principal_type = "user" if actor is not None else "system"
    campaign.quarantined_by_email_snapshot = actor.email if actor is not None else None
    campaign.quarantined_at = now
    campaign.quarantine_reason = normalized_reason
    _advance_campaign(campaign, now)
    db.flush()
    return campaign


def _collect_assignment_snapshots(
    db: Session,
    *,
    payload: AccessReviewCampaignCreate,
    now: datetime,
) -> list[AssignmentSnapshot]:
    user_ids = set(payload.user_ids)
    account_ids = set(payload.service_account_ids)
    snapshots: list[AssignmentSnapshot] = []
    snapshots.extend(_legacy_user_role_snapshots(db, user_ids=user_ids))
    snapshots.extend(_direct_user_role_snapshots(db, user_ids=user_ids))
    snapshots.extend(_group_membership_snapshots(db, user_ids=user_ids))
    snapshots.extend(
        _service_account_role_snapshots(db, service_account_ids=account_ids)
    )
    if payload.include_oidc_mappings:
        snapshots.extend(_oidc_role_mapping_snapshots(db))
        snapshots.extend(_oidc_group_mapping_snapshots(db))
    if payload.include_live_elevations:
        snapshots.extend(_live_elevation_snapshots(db, user_ids=user_ids, now=now))
    identities = {
        (snapshot.item_type, snapshot.assignment_id) for snapshot in snapshots
    }
    if len(identities) != len(snapshots):
        raise AccessReviewError(
            "Duplicate assignment identities were produced while building the campaign. No campaign was created."
        )
    return snapshots


def _count_assignment_snapshots(
    db: Session,
    *,
    payload: AccessReviewCampaignCreate,
    now: datetime,
) -> int:
    user_ids = set(payload.user_ids)
    account_ids = set(payload.service_account_ids)
    total = len(user_ids)
    if user_ids:
        total += int(
            db.scalar(
                select(func.count(IAMUserRoleAssignment.id)).where(
                    IAMUserRoleAssignment.user_id.in_(user_ids)
                )
            )
            or 0
        )
        total += int(
            db.scalar(
                select(func.count(IAMGroupMembership.id)).where(
                    IAMGroupMembership.user_id.in_(user_ids)
                )
            )
            or 0
        )
        if payload.include_live_elevations:
            total += int(
                db.scalar(
                    select(func.count(TemporaryElevation.id)).where(
                        TemporaryElevation.target_user_id.in_(user_ids),
                        TemporaryElevation.status == "approved",
                        TemporaryElevation.grant_started_at <= now,
                        TemporaryElevation.grant_expires_at > now,
                    )
                )
                or 0
            )
    if account_ids:
        total += int(
            db.scalar(
                select(func.count(ServiceAccountRoleAssignment.id)).where(
                    ServiceAccountRoleAssignment.service_account_id.in_(account_ids)
                )
            )
            or 0
        )
    if payload.include_oidc_mappings:
        total += int(db.scalar(select(func.count(OIDCRoleClaimMapping.id))) or 0)
        total += int(db.scalar(select(func.count(OIDCGroupClaimMapping.id))) or 0)
    return total


def _legacy_user_role_snapshots(
    db: Session,
    *,
    user_ids: set[uuid.UUID] | None = None,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if user_ids == set() or assignment_ids == set():
        return []
    query = (
        select(User, IAMRole)
        .join(IAMRole, IAMRole.key == User.role)
        .where(IAMRole.is_system.is_(True))
    )
    if user_ids is not None:
        query = query.where(User.id.in_(user_ids))
    if assignment_ids is not None:
        query = query.where(User.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=User)
    rows = db.execute(query.order_by(User.id)).all()
    expected_ids = user_ids
    if expected_ids is not None and {user.id for user, _role in rows} != expected_ids:
        raise AccessReviewError(
            "A selected user's built-in role is missing from sealed IAM policy. Repair IAM state before creating or applying the review."
        )
    permissions = _permissions_by_role(db, {role.id for _user, role in rows})
    return [
        _make_snapshot(
            ("legacy_user_role", user.id, "legacy", None),
            ("user", user.id, user.email),
            ("role", role.id, role.key, role.name, role.revision),
            permissions.get(role.id, ()),
            {
                "schema_version": 1,
                "account_active": user.is_active,
                "account_approved": user.is_approved,
                "provisioning_source": user.provisioning_source,
            },
            user.created_at,
            None,
        )
        for user, role in rows
    ]


def _direct_user_role_snapshots(
    db: Session,
    *,
    user_ids: set[uuid.UUID] | None = None,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if user_ids == set() or assignment_ids == set():
        return []
    query = (
        select(IAMUserRoleAssignment, User, IAMRole)
        .join(User, User.id == IAMUserRoleAssignment.user_id)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
    )
    if user_ids is not None:
        query = query.where(IAMUserRoleAssignment.user_id.in_(user_ids))
    if assignment_ids is not None:
        query = query.where(IAMUserRoleAssignment.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=IAMUserRoleAssignment)
    rows = db.execute(query.order_by(IAMUserRoleAssignment.id)).all()
    permissions = _permissions_by_role(db, {row[2].id for row in rows})
    result = []
    for assignment, user, role in rows:
        role_permissions = permissions.get(role.id, ())
        result.append(
            _make_snapshot(
                ("direct_user_role", assignment.id, assignment.source, None),
                ("user", user.id, user.email),
                ("role", role.id, role.key, role.name, role.revision),
                role_permissions,
                {
                    "schema_version": 1,
                    "source_key": assignment.source_key,
                    "oidc_role_mapping_id": _uuid_text(assignment.oidc_role_mapping_id),
                    "audit": _assignment_audit(assignment.assigned_by_user_id),
                },
                assignment.created_at,
                assignment.oidc_assertion_expires_at,
            )
        )
    return result


def _group_membership_snapshots(
    db: Session,
    *,
    user_ids: set[uuid.UUID] | None = None,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if user_ids == set() or assignment_ids == set():
        return []
    query = (
        select(IAMGroupMembership, User, IAMGroup)
        .join(User, User.id == IAMGroupMembership.user_id)
        .join(IAMGroup, IAMGroup.id == IAMGroupMembership.group_id)
    )
    if user_ids is not None:
        query = query.where(IAMGroupMembership.user_id.in_(user_ids))
    if assignment_ids is not None:
        query = query.where(IAMGroupMembership.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=IAMGroupMembership)
    rows = db.execute(query.order_by(IAMGroupMembership.id)).all()
    role_provenance = _group_role_provenance(db, {row[2].id for row in rows})
    result = []
    for membership, user, group in rows:
        roles = role_provenance.get(group.id, ())
        permissions = tuple(
            sorted({permission for role in roles for permission in role["permissions"]})
        )
        result.append(
            _make_snapshot(
                ("group_membership", membership.id, membership.source, None),
                ("user", user.id, user.email),
                ("group", group.id, group.key, group.name, group.revision),
                permissions,
                {
                    "schema_version": 1,
                    "source_key": membership.source_key,
                    "oidc_group_mapping_id": _uuid_text(
                        membership.oidc_group_mapping_id
                    ),
                    "group_roles": list(roles),
                    "audit": _assignment_audit(membership.assigned_by_user_id),
                },
                membership.created_at,
                membership.oidc_assertion_expires_at,
            )
        )
    return result


def _service_account_role_snapshots(
    db: Session,
    *,
    service_account_ids: set[uuid.UUID] | None = None,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if service_account_ids == set() or assignment_ids == set():
        return []
    query = (
        select(ServiceAccountRoleAssignment, ServiceAccount, IAMRole)
        .join(
            ServiceAccount,
            ServiceAccount.id == ServiceAccountRoleAssignment.service_account_id,
        )
        .join(IAMRole, IAMRole.id == ServiceAccountRoleAssignment.role_id)
    )
    if service_account_ids is not None:
        query = query.where(
            ServiceAccountRoleAssignment.service_account_id.in_(service_account_ids)
        )
    if assignment_ids is not None:
        query = query.where(ServiceAccountRoleAssignment.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=ServiceAccountRoleAssignment)
    rows = db.execute(query.order_by(ServiceAccountRoleAssignment.id)).all()
    permissions = _permissions_by_role(db, {row[2].id for row in rows})
    result = []
    for assignment, account, role in rows:
        role_permissions = permissions.get(role.id, ())
        result.append(
            _make_snapshot(
                ("service_account_role", assignment.id, "local", None),
                ("service_account", account.id, account.name),
                ("role", role.id, role.key, role.name, role.revision),
                role_permissions,
                {
                    "schema_version": 1,
                    "service_account_key": account.key,
                    "service_account_revision": account.revision,
                    "service_account_active": account.is_active,
                    "audit": _assignment_audit(assignment.assigned_by_user_id),
                },
                assignment.created_at,
                None,
            )
        )
    return result


def _oidc_role_mapping_snapshots(
    db: Session,
    *,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if assignment_ids == set():
        return []
    query = (
        select(
            OIDCRoleClaimMapping,
            OIDCClaimMappingSet,
            OIDCAccessPolicy,
            OIDCProvider,
            IAMRole,
        )
        .join(
            OIDCClaimMappingSet,
            OIDCClaimMappingSet.id == OIDCRoleClaimMapping.mapping_set_id,
        )
        .join(
            OIDCAccessPolicy,
            OIDCAccessPolicy.id == OIDCClaimMappingSet.access_policy_id,
        )
        .join(OIDCProvider, OIDCProvider.id == OIDCAccessPolicy.provider_id)
        .join(IAMRole, IAMRole.id == OIDCRoleClaimMapping.role_id)
    )
    if assignment_ids is not None:
        query = query.where(OIDCRoleClaimMapping.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=OIDCRoleClaimMapping)
    rows = db.execute(query.order_by(OIDCRoleClaimMapping.id)).all()
    permissions = _permissions_by_role(db, {row[4].id for row in rows})
    result = []
    for mapping, mapping_set, policy, provider, role in rows:
        role_permissions = permissions.get(role.id, ())
        result.append(
            _make_snapshot(
                ("oidc_role_mapping", mapping.id, "oidc", None),
                ("oidc_provider", provider.id, provider.name),
                ("role", role.id, role.key, role.name, role.revision),
                role_permissions,
                _oidc_mapping_provenance(mapping, mapping_set, policy, provider),
                mapping.created_at,
                None,
            )
        )
    return result


def _oidc_group_mapping_snapshots(
    db: Session,
    *,
    assignment_ids: set[uuid.UUID] | None = None,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if assignment_ids == set():
        return []
    query = (
        select(
            OIDCGroupClaimMapping,
            OIDCClaimMappingSet,
            OIDCAccessPolicy,
            OIDCProvider,
            IAMGroup,
        )
        .join(
            OIDCClaimMappingSet,
            OIDCClaimMappingSet.id == OIDCGroupClaimMapping.mapping_set_id,
        )
        .join(
            OIDCAccessPolicy,
            OIDCAccessPolicy.id == OIDCClaimMappingSet.access_policy_id,
        )
        .join(OIDCProvider, OIDCProvider.id == OIDCAccessPolicy.provider_id)
        .join(IAMGroup, IAMGroup.id == OIDCGroupClaimMapping.group_id)
    )
    if assignment_ids is not None:
        query = query.where(OIDCGroupClaimMapping.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=OIDCGroupClaimMapping)
    rows = db.execute(query.order_by(OIDCGroupClaimMapping.id)).all()
    role_provenance = _group_role_provenance(db, {row[4].id for row in rows})
    result = []
    for mapping, mapping_set, policy, provider, group in rows:
        roles = role_provenance.get(group.id, ())
        permissions = tuple(
            sorted({permission for role in roles for permission in role["permissions"]})
        )
        provenance = _oidc_mapping_provenance(mapping, mapping_set, policy, provider)
        provenance["group_roles"] = list(roles)
        result.append(
            _make_snapshot(
                ("oidc_group_mapping", mapping.id, "oidc", None),
                ("oidc_provider", provider.id, provider.name),
                ("group", group.id, group.key, group.name, group.revision),
                permissions,
                provenance,
                mapping.created_at,
                None,
            )
        )
    return result


def _live_elevation_snapshots(
    db: Session,
    *,
    user_ids: set[uuid.UUID] | None = None,
    assignment_ids: set[uuid.UUID] | None = None,
    now: datetime,
    lock: bool = False,
) -> list[AssignmentSnapshot]:
    if user_ids == set() or assignment_ids == set():
        return []
    query = (
        select(TemporaryElevation, User, IAMRole)
        .join(User, User.id == TemporaryElevation.target_user_id)
        .join(IAMRole, IAMRole.id == TemporaryElevation.role_id)
        .where(
            TemporaryElevation.status == "approved",
            TemporaryElevation.grant_started_at <= now,
            TemporaryElevation.grant_expires_at > now,
        )
    )
    if user_ids is not None:
        query = query.where(TemporaryElevation.target_user_id.in_(user_ids))
    if assignment_ids is not None:
        query = query.where(TemporaryElevation.id.in_(assignment_ids))
    if lock:
        query = query.with_for_update(of=TemporaryElevation)
    rows = db.execute(query.order_by(TemporaryElevation.id)).all()
    elevation_ids = {row[0].id for row in rows}
    permission_rows = (
        db.execute(
            select(
                TemporaryElevationPermission.elevation_id,
                TemporaryElevationPermission.permission,
            )
            .where(TemporaryElevationPermission.elevation_id.in_(elevation_ids))
            .order_by(
                TemporaryElevationPermission.elevation_id,
                TemporaryElevationPermission.permission,
            )
        ).all()
        if elevation_ids
        else []
    )
    permissions: dict[uuid.UUID, list[str]] = {value: [] for value in elevation_ids}
    for elevation_id, permission in permission_rows:
        permissions[elevation_id].append(permission)
    result = []
    for elevation, user, role in rows:
        permission_snapshot = tuple(permissions.get(elevation.id, []))
        result.append(
            _make_snapshot(
                ("live_elevation", elevation.id, "temporary", elevation.revision),
                ("user", user.id, user.email),
                (
                    "role",
                    role.id,
                    elevation.role_key_snapshot,
                    elevation.role_name_snapshot,
                    role.revision,
                ),
                permission_snapshot,
                {
                    "schema_version": 1,
                    "role_revision_at_grant": elevation.role_revision_snapshot,
                    "audit": {
                        "requested_by_user_id": _uuid_text(
                            elevation.requested_by_user_id
                        ),
                        "decided_by_user_id": _uuid_text(elevation.decided_by_user_id),
                        "request_reason": elevation.request_reason,
                        "decision_reason": elevation.decision_reason,
                    },
                },
                elevation.created_at,
                elevation.grant_expires_at,
            )
        )
    return result


def current_access_review_assignment(
    db: Session,
    *,
    item: AccessReviewItem,
    now: datetime,
    lock: bool,
) -> AssignmentSnapshot | None:
    assignment_ids = {item.assignment_id}
    if item.item_type == "legacy_user_role":
        values = _legacy_user_role_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "direct_user_role":
        values = _direct_user_role_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "group_membership":
        values = _group_membership_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "service_account_role":
        values = _service_account_role_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "oidc_role_mapping":
        values = _oidc_role_mapping_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "oidc_group_mapping":
        values = _oidc_group_mapping_snapshots(
            db, assignment_ids=assignment_ids, lock=lock
        )
    elif item.item_type == "live_elevation":
        values = _live_elevation_snapshots(
            db, assignment_ids=assignment_ids, now=now, lock=lock
        )
    else:
        raise AccessReviewError(
            f"Unsupported access-review item type {item.item_type!r}. No access was changed."
        )
    if len(values) > 1:
        raise AccessReviewError(
            "Assignment revalidation returned duplicate rows. No access was changed."
        )
    return values[0] if values else None


def _make_snapshot(*args) -> AssignmentSnapshot:
    _require_json_size(args[3], "Assignment permissions", MAX_PERMISSION_JSON_BYTES)
    _require_json_size(args[4], "Assignment provenance")
    return _build_snapshot(*args)


def _permissions_by_role(
    db: Session, role_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, ...]]:
    result: dict[uuid.UUID, list[str]] = {role_id: [] for role_id in role_ids}
    if not role_ids:
        return {}
    rows = db.execute(
        select(IAMRolePermission.role_id, IAMRolePermission.permission)
        .where(IAMRolePermission.role_id.in_(role_ids))
        .order_by(IAMRolePermission.role_id, IAMRolePermission.permission)
    ).all()
    for role_id, permission in rows:
        result[role_id].append(permission)
    return {role_id: tuple(values) for role_id, values in result.items()}


def _group_role_provenance(
    db: Session, group_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[dict[str, object], ...]]:
    if not group_ids:
        return {}
    rows = db.execute(
        select(IAMGroupRoleAssignment, IAMRole)
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .where(IAMGroupRoleAssignment.group_id.in_(group_ids))
        .order_by(IAMGroupRoleAssignment.group_id, IAMGroupRoleAssignment.id)
    ).all()
    permissions = _permissions_by_role(db, {row[1].id for row in rows})
    result: dict[uuid.UUID, list[dict[str, object]]] = {
        group_id: [] for group_id in group_ids
    }
    for assignment, role in rows:
        result[assignment.group_id].append(
            {
                "assignment_id": str(assignment.id),
                "role_id": str(role.id),
                "role_key": role.key,
                "role_name": role.name,
                "role_revision": role.revision,
                "permissions": list(permissions.get(role.id, ())),
                "created_at": _iso(assignment.created_at),
                "audit": _assignment_audit(assignment.assigned_by_user_id),
            }
        )
    return {group_id: tuple(values) for group_id, values in result.items()}


def _assignment_audit(user_id: uuid.UUID | None) -> dict[str, str | None]:
    return {"assigned_by_user_id": _uuid_text(user_id)}


def _oidc_mapping_provenance(
    mapping,
    mapping_set: OIDCClaimMappingSet,
    policy: OIDCAccessPolicy,
    provider: OIDCProvider,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mapping_set_id": str(mapping_set.id),
        "mapping_set_key": mapping_set.key,
        "mapping_set_name": mapping_set.name,
        "mapping_set_revision": mapping_set.revision,
        "mapping_set_enabled": mapping_set.enabled,
        "claim_path": mapping_set.claim_path,
        "missing_claim_behavior": mapping_set.missing_claim_behavior,
        "claim_value": mapping.claim_value,
        "source_key": mapping.source_key,
        "access_policy_id": str(policy.id),
        "access_policy_revision": policy.revision,
        "access_policy_enabled": policy.enabled,
        "provider_system_key": provider.system_key,
        "provider_enabled": provider.enabled,
        "provider_config_revision": provider.config_revision,
        "provider_access_policy_generation": provider.oidc_access_policy_generation,
    }


def _require_selected_principals(
    db: Session, payload: AccessReviewCampaignCreate
) -> None:
    selected_users = set(payload.user_ids)
    found_users = (
        set(db.scalars(select(User.id).where(User.id.in_(selected_users))).all())
        if selected_users
        else set()
    )
    selected_accounts = set(payload.service_account_ids)
    found_accounts = (
        set(
            db.scalars(
                select(ServiceAccount.id).where(
                    ServiceAccount.id.in_(selected_accounts)
                )
            ).all()
        )
        if selected_accounts
        else set()
    )
    if found_users != selected_users or found_accounts != selected_accounts:
        raise AccessReviewSelectionInvalid(
            "One or more selected users or service accounts no longer exist. Reload the principal list and retry."
        )


def _decision_count(db: Session, campaign_id: uuid.UUID) -> int:
    latest_decision_sequence = (
        select(
            AccessReviewDecision.item_id.label("item_id"),
            func.max(AccessReviewDecision.sequence).label("sequence"),
        )
        .where(AccessReviewDecision.campaign_id == campaign_id)
        .group_by(AccessReviewDecision.item_id)
        .subquery()
    )
    return int(
        db.scalar(
            select(func.count(AccessReviewDecision.id)).join(
                latest_decision_sequence,
                (latest_decision_sequence.c.item_id == AccessReviewDecision.item_id)
                & (
                    latest_decision_sequence.c.sequence == AccessReviewDecision.sequence
                ),
            )
        )
        or 0
    )


def _terminal_apply_count(db: Session, campaign_id: uuid.UUID) -> int:
    latest_receipt_attempt = (
        select(
            AccessReviewApplyReceipt.item_id.label("item_id"),
            func.max(AccessReviewApplyReceipt.attempt).label("attempt"),
        )
        .where(AccessReviewApplyReceipt.campaign_id == campaign_id)
        .group_by(AccessReviewApplyReceipt.item_id)
        .subquery()
    )
    return int(
        db.scalar(
            select(func.count(AccessReviewApplyReceipt.id))
            .join(
                latest_receipt_attempt,
                (latest_receipt_attempt.c.item_id == AccessReviewApplyReceipt.item_id)
                & (
                    latest_receipt_attempt.c.attempt == AccessReviewApplyReceipt.attempt
                ),
            )
            .where(
                AccessReviewApplyReceipt.outcome.in_(
                    ACCESS_REVIEW_TERMINAL_APPLY_OUTCOMES
                )
            )
        )
        or 0
    )


def _lock_campaign(db: Session, campaign_id: uuid.UUID) -> AccessReviewCampaign:
    campaign = db.scalar(
        select(AccessReviewCampaign)
        .where(AccessReviewCampaign.id == campaign_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        raise AccessReviewNotFound("Access-review campaign not found.")
    return campaign


def _require_revision(campaign: AccessReviewCampaign, expected_revision: int) -> None:
    if campaign.revision != expected_revision:
        raise AccessReviewRevisionConflict(campaign)


def _advance_campaign(campaign: AccessReviewCampaign, now: datetime) -> None:
    campaign.revision += 1
    campaign.updated_at = now


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(database_clock(db)))
    if not isinstance(value, datetime):
        raise AccessReviewError(
            "The database clock could not be read. No access-review state was changed."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_json_size(
    value: object, label: str, limit: int = MAX_SNAPSHOT_JSON_BYTES
) -> None:
    size = len(access_review_snapshot_json(value).encode("utf-8"))
    if size > limit:
        raise AccessReviewLimitExceeded(
            f"{label} is {size:,} bytes; the maximum is {limit:,} bytes. Narrow the campaign scope."
        )
