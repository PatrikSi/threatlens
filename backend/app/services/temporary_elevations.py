from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.iam import IAMRole, IAMRolePermission
from app.models.temporary_elevation import (
    TemporaryElevation,
    TemporaryElevationPermission,
)
from app.models.user import User
from app.schemas.temporary_elevation import (
    ElevationCloseRequest,
    ElevationDecisionRequest,
    ElevationRequestCreate,
    TemporaryElevationListResponse,
    TemporaryElevationResponse,
)
from app.services.authorization import (
    AuthorizationContext,
    bump_iam_policy_revision,
    database_clock,
    lock_iam_policy_for_mutation,
)
from app.services.iam_delegation import require_durable_delegable_permissions


ELEVATION_REQUEST_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_LIVE_ELEVATIONS_PER_TARGET = 10


class TemporaryElevationError(RuntimeError):
    code = "temporary_elevation_error"


class TemporaryElevationNotFound(TemporaryElevationError):
    code = "temporary_elevation_not_found"


class TemporaryElevationConflict(TemporaryElevationError):
    code = "temporary_elevation_conflict"


class TemporaryElevationForbidden(TemporaryElevationError):
    code = "temporary_elevation_forbidden"


class TemporaryElevationDuplicate(TemporaryElevationConflict):
    code = "temporary_elevation_duplicate"


class TemporaryElevationExpired(TemporaryElevationConflict):
    code = "temporary_elevation_expired"


class TemporaryElevationLimitReached(TemporaryElevationConflict):
    code = "temporary_elevation_limit_reached"


class TemporaryElevationRoleChanged(TemporaryElevationConflict):
    code = "temporary_elevation_role_changed"


class TemporaryElevationStateConflict(TemporaryElevationConflict):
    code = "temporary_elevation_state_conflict"


class TemporaryElevationTargetIneligible(TemporaryElevationConflict):
    code = "temporary_elevation_target_ineligible"


class TemporaryElevationRevisionConflict(TemporaryElevationConflict):
    code = "temporary_elevation_revision_conflict"

    def __init__(self, elevation: TemporaryElevation) -> None:
        self.current_revision = elevation.revision
        super().__init__(
            "This elevation request changed after it was loaded. Reload it and retry."
        )


@dataclass(frozen=True)
class ElevationMutationResult:
    elevation: TemporaryElevation
    previous_status: str | None = None


def create_temporary_elevation(
    db: Session,
    *,
    requester: User,
    payload: ElevationRequestCreate,
    can_request_for_others: bool,
) -> ElevationMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    target_user_id = payload.target_user_id or requester.id
    if target_user_id != requester.id and not can_request_for_others:
        raise TemporaryElevationForbidden(
            "Requesting temporary access for another person requires elevation approval authority."
        )

    target = db.scalar(
        select(User)
        .where(User.id == target_user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise TemporaryElevationNotFound("The target user was not found.")
    if not target.is_active or not target.is_approved:
        raise TemporaryElevationTargetIneligible(
            "Temporary access can be requested only for an active, approved user."
        )

    role = db.scalar(
        select(IAMRole)
        .where(IAMRole.id == payload.role_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if role is None:
        raise TemporaryElevationNotFound("The requested role was not found.")
    if role.is_system:
        raise TemporaryElevationRoleChanged(
            "Built-in roles cannot be granted temporarily. Create a bounded custom role instead."
        )
    if role.revision != payload.expected_role_revision:
        raise TemporaryElevationRoleChanged(
            "The requested role changed after it was selected. Reload the role and submit a new request."
        )
    permissions = list(
        db.scalars(
            select(IAMRolePermission.permission)
            .where(IAMRolePermission.role_id == role.id)
            .order_by(IAMRolePermission.permission)
        ).all()
    )

    live_count = int(
        db.scalar(
            select(func.count(TemporaryElevation.id)).where(
                TemporaryElevation.target_user_id == target.id,
                or_(
                    (
                        (TemporaryElevation.status == "pending")
                        & (TemporaryElevation.request_expires_at > now)
                    ),
                    (
                        (TemporaryElevation.status == "approved")
                        & (TemporaryElevation.grant_expires_at > now)
                    ),
                ),
            )
        )
        or 0
    )
    if live_count >= MAX_LIVE_ELEVATIONS_PER_TARGET:
        raise TemporaryElevationLimitReached(
            "This user already has the maximum number of active or pending elevations. Resolve, revoke, or let one expire before creating another."
        )
    duplicate = db.scalar(
        select(TemporaryElevation.id).where(
            TemporaryElevation.target_user_id == target.id,
            TemporaryElevation.role_id == role.id,
            or_(
                (
                    (TemporaryElevation.status == "pending")
                    & (TemporaryElevation.request_expires_at > now)
                ),
                (
                    (TemporaryElevation.status == "approved")
                    & (TemporaryElevation.grant_started_at <= now)
                    & (TemporaryElevation.grant_expires_at > now)
                ),
            ),
        )
    )
    if duplicate is not None:
        raise TemporaryElevationDuplicate(
            "An active or pending elevation for this user and role already exists."
        )

    elevation = TemporaryElevation(
        target_user_id=target.id,
        target_email_snapshot=target.email,
        role_id=role.id,
        role_key_snapshot=role.key,
        role_name_snapshot=role.name,
        role_revision_snapshot=role.revision,
        requested_by_user_id=requester.id,
        requested_by_email_snapshot=requester.email,
        requested_duration_seconds=payload.duration_seconds,
        request_reason=payload.reason,
        request_expires_at=now + timedelta(seconds=ELEVATION_REQUEST_TTL_SECONDS),
        status="pending",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(elevation)
    db.flush()
    db.add_all(
        TemporaryElevationPermission(
            elevation_id=elevation.id,
            permission=permission,
        )
        for permission in permissions
    )
    db.flush()
    return ElevationMutationResult(elevation=elevation)


def decide_temporary_elevation(
    db: Session,
    *,
    elevation_id: uuid.UUID,
    approver: User,
    approver_authorization: AuthorizationContext,
    payload: ElevationDecisionRequest,
) -> ElevationMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    elevation = _lock_elevation(db, elevation_id)
    _require_revision(elevation, payload.expected_revision)
    if elevation.status != "pending":
        raise TemporaryElevationStateConflict(
            f"This elevation request is already {effective_elevation_status(elevation, now)}."
        )
    if elevation.request_expires_at <= now:
        raise TemporaryElevationExpired(
            "This elevation request expired before a decision was recorded. Create a new request."
        )
    if approver.id in {elevation.requested_by_user_id, elevation.target_user_id}:
        raise TemporaryElevationForbidden(
            "An elevation request must be decided by a different person than its requester and target."
        )

    previous_status = elevation.status
    elevation.decided_by_user_id = approver.id
    elevation.decided_by_email_snapshot = approver.email
    elevation.decided_at = now
    elevation.decision_reason = payload.reason
    if payload.approve:
        target = db.scalar(
            select(User)
            .where(User.id == elevation.target_user_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise TemporaryElevationNotFound("The target user no longer exists.")
        if not target.is_active or not target.is_approved:
            raise TemporaryElevationTargetIneligible(
                "The target user is no longer active and approved. Deny this request or restore the account first."
            )
        if elevation.role_id is None:
            raise TemporaryElevationRoleChanged(
                "The requested role was deleted. Deny this request and create a new one for an existing role."
            )
        role = db.scalar(
            select(IAMRole)
            .where(IAMRole.id == elevation.role_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if role is None or role.is_system:
            raise TemporaryElevationRoleChanged(
                "The requested custom role is no longer available. Deny this request and create a new one."
            )
        if role.revision != elevation.role_revision_snapshot:
            raise TemporaryElevationRoleChanged(
                "The requested role changed after this request was created. Deny it and submit a new request against the current role revision."
            )
        permission_snapshot = db.scalars(
            select(TemporaryElevationPermission.permission).where(
                TemporaryElevationPermission.elevation_id == elevation.id
            )
        ).all()
        require_durable_delegable_permissions(
            approver_authorization, permission_snapshot
        )
        duplicate = db.scalar(
            select(TemporaryElevation.id).where(
                TemporaryElevation.id != elevation.id,
                TemporaryElevation.target_user_id == elevation.target_user_id,
                TemporaryElevation.role_id == role.id,
                TemporaryElevation.status == "approved",
                TemporaryElevation.grant_started_at <= now,
                TemporaryElevation.grant_expires_at > now,
            )
        )
        if duplicate is not None:
            raise TemporaryElevationDuplicate(
                "This user already has an active elevation for the requested role."
            )
        elevation.status = "approved"
        elevation.grant_started_at = now
        elevation.grant_expires_at = now + timedelta(
            seconds=elevation.requested_duration_seconds
        )
        bump_iam_policy_revision(db)
    else:
        elevation.status = "denied"
    elevation.revision += 1
    elevation.updated_at = now
    db.add(elevation)
    db.flush()
    return ElevationMutationResult(elevation=elevation, previous_status=previous_status)


def close_temporary_elevation(
    db: Session,
    *,
    elevation_id: uuid.UUID,
    actor: User,
    can_manage_others: bool,
    payload: ElevationCloseRequest,
) -> ElevationMutationResult:
    lock_iam_policy_for_mutation(db)
    now = _database_now(db)
    elevation = _lock_elevation(db, elevation_id)
    _require_revision(elevation, payload.expected_revision)
    if (
        actor.id
        not in {
            elevation.target_user_id,
            elevation.requested_by_user_id,
        }
        and not can_manage_others
    ):
        raise TemporaryElevationForbidden(
            "Only the requester, target user, or an elevation approver can close this request."
        )

    previous_status = elevation.status
    access_changed = False
    if elevation.status == "pending":
        if elevation.request_expires_at <= now:
            raise TemporaryElevationExpired(
                "This pending request has already expired and no longer needs cancellation."
            )
        elevation.status = "cancelled"
    elif elevation.status == "approved":
        if elevation.grant_expires_at is None or elevation.grant_expires_at <= now:
            raise TemporaryElevationExpired(
                "This temporary grant has already expired and no longer needs revocation."
            )
        elevation.status = "revoked"
        access_changed = True
    else:
        raise TemporaryElevationStateConflict(
            f"This elevation request is already {effective_elevation_status(elevation, now)}."
        )

    elevation.closed_by_user_id = actor.id
    elevation.closed_by_principal_type = "user"
    elevation.closed_by_email_snapshot = actor.email
    elevation.closed_at = now
    elevation.close_reason = payload.reason
    elevation.revision += 1
    elevation.updated_at = now
    db.add(elevation)
    if access_changed:
        bump_iam_policy_revision(db)
    db.flush()
    return ElevationMutationResult(elevation=elevation, previous_status=previous_status)


def get_temporary_elevation_response(
    db: Session, elevation_id: uuid.UUID
) -> TemporaryElevationResponse:
    row = _response_row_query().where(TemporaryElevation.id == elevation_id)
    result = db.execute(row).one_or_none()
    if result is None:
        raise TemporaryElevationNotFound("Temporary elevation request not found.")
    permissions = _permission_snapshots(db, [elevation_id]).get(elevation_id, [])
    return _response_from_row(result, _database_now(db), permissions)


def list_temporary_elevations(
    db: Session,
    *,
    page: int,
    page_size: int,
    target_user_id: uuid.UUID | None = None,
    stored_status: str | None = None,
) -> TemporaryElevationListResponse:
    filters = []
    if target_user_id is not None:
        filters.append(TemporaryElevation.target_user_id == target_user_id)
    if stored_status is not None:
        filters.append(TemporaryElevation.status == stored_status)
    total = int(
        db.scalar(select(func.count(TemporaryElevation.id)).where(*filters)) or 0
    )
    rows = db.execute(
        _response_row_query()
        .where(*filters)
        .order_by(TemporaryElevation.created_at.desc(), TemporaryElevation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    now = _database_now(db)
    permissions = _permission_snapshots(db, [row[0].id for row in rows])
    return TemporaryElevationListResponse(
        elevations=[
            _response_from_row(row, now, permissions.get(row[0].id, [])) for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def effective_elevation_status(elevation: TemporaryElevation, now: datetime) -> str:
    if elevation.status == "pending" and elevation.request_expires_at <= now:
        return "expired"
    if (
        elevation.status == "approved"
        and elevation.grant_expires_at is not None
        and elevation.grant_expires_at <= now
    ):
        return "expired"
    return elevation.status


def active_elevation_ids_for_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    clock = database_clock(db)
    return list(
        db.scalars(
            select(TemporaryElevation.id)
            .where(
                TemporaryElevation.target_user_id == user_id,
                TemporaryElevation.status == "approved",
                TemporaryElevation.grant_started_at <= clock,
                TemporaryElevation.grant_expires_at > clock,
            )
            .order_by(TemporaryElevation.id)
        ).all()
    )


def role_has_live_elevation_reference(db: Session, role_id: uuid.UUID) -> bool:
    clock = database_clock(db)
    return bool(
        db.scalar(
            select(TemporaryElevation.id).where(
                TemporaryElevation.role_id == role_id,
                or_(
                    (
                        (TemporaryElevation.status == "pending")
                        & (TemporaryElevation.request_expires_at > clock)
                    ),
                    (
                        (TemporaryElevation.status == "approved")
                        & (TemporaryElevation.grant_expires_at > clock)
                    ),
                ),
            )
        )
    )


def _lock_elevation(db: Session, elevation_id: uuid.UUID) -> TemporaryElevation:
    elevation = db.scalar(
        select(TemporaryElevation)
        .where(TemporaryElevation.id == elevation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if elevation is None:
        raise TemporaryElevationNotFound("Temporary elevation request not found.")
    return elevation


def _require_revision(elevation: TemporaryElevation, expected_revision: int) -> None:
    if elevation.revision != expected_revision:
        raise TemporaryElevationRevisionConflict(elevation)


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(database_clock(db)))
    if not isinstance(value, datetime):
        raise TemporaryElevationError(
            "The database clock could not be read. No elevation state was changed."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _response_row_query():
    target = aliased(User)
    requester = aliased(User)
    decider = aliased(User)
    closer = aliased(User)
    return (
        select(
            TemporaryElevation,
            target.email.label("target_email"),
            requester.email.label("requester_email"),
            decider.email.label("decider_email"),
            closer.email.label("closer_email"),
        )
        .outerjoin(target, target.id == TemporaryElevation.target_user_id)
        .outerjoin(requester, requester.id == TemporaryElevation.requested_by_user_id)
        .outerjoin(decider, decider.id == TemporaryElevation.decided_by_user_id)
        .outerjoin(closer, closer.id == TemporaryElevation.closed_by_user_id)
    )


def _permission_snapshots(
    db: Session, elevation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    snapshots: dict[uuid.UUID, list[str]] = {value: [] for value in elevation_ids}
    if not elevation_ids:
        return snapshots
    rows = db.execute(
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
    for elevation_id, permission in rows:
        snapshots[elevation_id].append(permission)
    return snapshots


def _response_from_row(
    row, now: datetime, permission_snapshot: list[str]
) -> TemporaryElevationResponse:
    elevation = row[0]
    return TemporaryElevationResponse(
        id=elevation.id,
        target_user_id=elevation.target_user_id,
        target_email=elevation.target_email_snapshot,
        target_current_email=row.target_email,
        role_id=elevation.role_id,
        role_key=elevation.role_key_snapshot,
        role_name=elevation.role_name_snapshot,
        role_revision_snapshot=elevation.role_revision_snapshot,
        permission_snapshot=permission_snapshot,
        requested_by_user_id=elevation.requested_by_user_id,
        requested_by_email=elevation.requested_by_email_snapshot,
        requested_by_current_email=row.requester_email,
        requested_duration_seconds=elevation.requested_duration_seconds,
        request_reason=elevation.request_reason,
        request_expires_at=elevation.request_expires_at,
        stored_status=elevation.status,
        status=effective_elevation_status(elevation, now),
        revision=elevation.revision,
        decided_by_user_id=elevation.decided_by_user_id,
        decided_by_email=elevation.decided_by_email_snapshot,
        decided_by_current_email=row.decider_email,
        decided_at=elevation.decided_at,
        decision_reason=elevation.decision_reason,
        grant_started_at=elevation.grant_started_at,
        grant_expires_at=elevation.grant_expires_at,
        closed_by_user_id=elevation.closed_by_user_id,
        closed_by_principal_type=elevation.closed_by_principal_type,
        closed_by_email=elevation.closed_by_email_snapshot,
        closed_by_current_email=row.closer_email,
        closed_at=elevation.closed_at,
        close_reason=elevation.close_reason,
        created_at=elevation.created_at,
        updated_at=elevation.updated_at,
    )


__all__ = [
    "ElevationMutationResult",
    "TemporaryElevationConflict",
    "TemporaryElevationDuplicate",
    "TemporaryElevationError",
    "TemporaryElevationExpired",
    "TemporaryElevationForbidden",
    "TemporaryElevationLimitReached",
    "TemporaryElevationNotFound",
    "TemporaryElevationRevisionConflict",
    "TemporaryElevationRoleChanged",
    "TemporaryElevationStateConflict",
    "TemporaryElevationTargetIneligible",
    "active_elevation_ids_for_user",
    "close_temporary_elevation",
    "create_temporary_elevation",
    "decide_temporary_elevation",
    "effective_elevation_status",
    "get_temporary_elevation_response",
    "list_temporary_elevations",
    "role_has_live_elevation_reference",
]
