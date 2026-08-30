from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import (
    SERVICE_ACCOUNT_PERMISSION_IDS,
    WILDCARD_PERMISSION_IDS,
    expand_permission_grants,
)
from app.models.iam import IAMRole, IAMRolePermission
from app.models.service_account import (
    ServiceAccount,
    ServiceAccountCredential,
    ServiceAccountRoleAssignment,
)
from app.schemas.service_account import (
    ServiceAccountCreateRequest,
    ServiceAccountCredentialIssueRequest,
    ServiceAccountCredentialResponse,
    ServiceAccountResponse,
    ServiceAccountRoleAssignmentRequest,
    ServiceAccountRoleAssignmentResponse,
    ServiceAccountUpdateRequest,
)
from app.services.authorization import bump_iam_policy_revision
from app.services.authorization import AuthorizationContext
from app.services.iam_delegation import (
    IAMDelegationDenied,
    require_delegable_permissions,
)


SERVICE_ACCOUNT_TOKEN_MARKER = "tlsa"
SERVICE_ACCOUNT_CREDENTIAL_MIN_LIFETIME = timedelta(days=1)
SERVICE_ACCOUNT_CREDENTIAL_MAX_LIFETIME = timedelta(days=365)
SERVICE_ACCOUNT_CREDENTIAL_GENERATION_ATTEMPTS = 3
SERVICE_ACCOUNT_ROTATION_OVERLAP = timedelta(hours=24)

# Machine identities are intentionally data-plane only. Adding a permission to the
# global catalog never makes it available to service accounts automatically.
SAFE_SERVICE_ACCOUNT_PERMISSIONS = SERVICE_ACCOUNT_PERMISSION_IDS


class ServiceAccountError(RuntimeError):
    code = "service_account_error"


class ServiceAccountNotFound(ServiceAccountError):
    code = "service_account_not_found"


class ServiceAccountConflict(ServiceAccountError):
    code = "service_account_conflict"


class ServiceAccountRevisionConflict(ServiceAccountError):
    code = "service_account_revision_conflict"

    def __init__(self, account: ServiceAccount):
        super().__init__(
            "This service account changed after it was loaded. Reload it and apply "
            "the intended change again."
        )
        self.current_revision = account.revision


class ServiceAccountInactive(ServiceAccountError):
    code = "service_account_inactive"


class ServiceAccountMustBeDisabled(ServiceAccountError):
    code = "service_account_must_be_disabled"


class ServiceAccountDelegationDenied(ServiceAccountError):
    code = "service_account_delegation_denied"

    def __init__(self, missing_permissions: tuple[str, ...]):
        self.missing_permissions = missing_permissions
        super().__init__(
            "You may grant a service account only permissions currently available "
            "to this authenticated session."
        )


class ServiceAccountRoleNotFound(ServiceAccountError):
    code = "service_account_role_not_found"


class ServiceAccountSystemRoleRejected(ServiceAccountError):
    code = "service_account_system_role_rejected"


class ServiceAccountRoleRevisionConflict(ServiceAccountError):
    code = "service_account_role_revision_conflict"

    def __init__(self, role: IAMRole):
        super().__init__(
            "This role changed after it was loaded. Reload it before assigning it."
        )
        self.current_revision = role.revision


class ServiceAccountRoleAssignmentConflict(ServiceAccountError):
    code = "service_account_role_assignment_conflict"


class ServiceAccountRoleAssignmentNotFound(ServiceAccountError):
    code = "service_account_role_assignment_not_found"


class ServiceAccountRoleContainsWildcard(ServiceAccountError):
    code = "service_account_role_contains_wildcard"


class ServiceAccountRoleUnsafePermissions(ServiceAccountError):
    code = "service_account_role_unsafe_permissions"

    def __init__(self, permissions: list[str]):
        self.blocked_permissions = permissions
        super().__init__(
            "This role contains permissions that are not available to service "
            f"accounts: {', '.join(permissions)}."
        )


class ServiceAccountScopeNotAllowed(ServiceAccountError):
    code = "service_account_scope_not_allowed"

    def __init__(self, scopes: list[str]):
        super().__init__(
            "Service-account credentials cannot contain these permissions: "
            f"{', '.join(scopes)}."
        )
        self.scopes = scopes


class ServiceAccountScopeEscalation(ServiceAccountError):
    code = "service_account_scope_escalation"

    def __init__(self, scopes: list[str]):
        super().__init__(
            "Requested credential scopes exceed the service account's effective "
            f"safe role permissions: {', '.join(scopes)}."
        )
        self.scopes = scopes


class ServiceAccountCredentialNotFound(ServiceAccountError):
    code = "service_account_credential_not_found"


class ServiceAccountCredentialRevoked(ServiceAccountError):
    code = "service_account_credential_revoked"


class ServiceAccountCredentialOperationAlreadyCommitted(ServiceAccountError):
    operation_label = "credential operation"

    def __init__(
        self,
        credential: ServiceAccountCredential,
        account: ServiceAccount,
    ) -> None:
        self.credential_id = credential.id
        self.current_revision = account.revision
        super().__init__(
            f"This {self.operation_label} was already committed. The one-time secret "
            "cannot be shown again. Locate the credential by the returned credential "
            "ID, revoke it if the secret was lost, and retry with a new idempotency key."
        )


class ServiceAccountCredentialIssueAlreadyCommitted(
    ServiceAccountCredentialOperationAlreadyCommitted
):
    code = "service_account_credential_issue_already_committed"
    operation_label = "credential issuance"


class ServiceAccountRotationAlreadyCommitted(
    ServiceAccountCredentialOperationAlreadyCommitted
):
    code = "service_account_rotation_already_committed"
    operation_label = "credential rotation"


class ServiceAccountIdempotencyConflict(ServiceAccountError):
    code = "service_account_idempotency_conflict"


class ServiceAccountIdempotencyKeyInvalid(ServiceAccountError):
    code = "service_account_idempotency_key_invalid"


class ServiceAccountCredentialGenerationFailed(ServiceAccountError):
    code = "service_account_credential_generation_failed"


@dataclass(frozen=True)
class ServiceAccountMutationResult:
    account: ServiceAccount
    changed: bool
    affected_count: int = 0


@dataclass(frozen=True)
class ServiceAccountCredentialIssueResult:
    account: ServiceAccount
    credential: ServiceAccountCredential
    token: str
    previous_credential_expires_at: datetime | None = None


def list_service_accounts(
    db: Session,
    *,
    page: int,
    page_size: int,
) -> tuple[list[ServiceAccountResponse], int]:
    total = int(db.scalar(select(func.count(ServiceAccount.id))) or 0)
    accounts = list(
        db.scalars(
            select(ServiceAccount)
            .order_by(
                ServiceAccount.is_active.desc(),
                ServiceAccount.name,
                ServiceAccount.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return _service_account_page_responses(db, accounts), total


def get_service_account_response(
    db: Session, service_account_id: uuid.UUID
) -> ServiceAccountResponse:
    account = db.get(ServiceAccount, service_account_id)
    if account is None:
        raise ServiceAccountNotFound("Service account not found.")
    return service_account_response(db, account)


def create_service_account(
    db: Session,
    *,
    payload: ServiceAccountCreateRequest,
    actor_user_id: uuid.UUID,
) -> ServiceAccount:
    account = ServiceAccount(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        is_active=True,
        revision=1,
        created_by_user_id=actor_user_id,
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) == "uq_service_accounts_key":
            raise ServiceAccountConflict(
                "A service account with this key already exists."
            ) from exc
        raise
    bump_iam_policy_revision(db)
    return account


def update_service_account(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    payload: ServiceAccountUpdateRequest,
) -> ServiceAccount:
    account = _lock_service_account(db, service_account_id)
    _require_revision(account, payload.expected_revision)
    if payload.name is not None:
        account.name = payload.name
    if payload.description is not None:
        account.description = payload.description
    account.revision += 1
    db.add(account)
    bump_iam_policy_revision(db)
    db.flush()
    return account


def disable_service_account(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    expected_revision: int,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> ServiceAccountMutationResult:
    account = _lock_service_account(db, service_account_id)
    if not account.is_active:
        return ServiceAccountMutationResult(account=account, changed=False)
    _require_revision(account, expected_revision)
    disabled_at = now or datetime.now(timezone.utc)
    account.is_active = False
    account.disabled_at = disabled_at
    account.disabled_by_user_id = actor_user_id
    account.revision += 1
    revoked = db.execute(
        update(ServiceAccountCredential)
        .where(
            ServiceAccountCredential.service_account_id == account.id,
            ServiceAccountCredential.revoked_at.is_(None),
        )
        .values(revoked_at=disabled_at, revoked_by_user_id=actor_user_id)
    )
    db.add(account)
    bump_iam_policy_revision(db)
    db.flush()
    return ServiceAccountMutationResult(
        account=account,
        changed=True,
        affected_count=int(revoked.rowcount or 0),
    )


def delete_service_account(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    expected_revision: int,
) -> ServiceAccount:
    account = _lock_service_account(db, service_account_id)
    _require_revision(account, expected_revision)
    if account.is_active:
        raise ServiceAccountMustBeDisabled(
            "Disable the service account and revoke its credentials before deleting it."
        )
    db.delete(account)
    bump_iam_policy_revision(db)
    db.flush()
    return account


def list_role_assignments(
    db: Session, service_account_id: uuid.UUID
) -> list[ServiceAccountRoleAssignmentResponse]:
    _require_service_account(db, service_account_id)
    rows = db.execute(
        select(ServiceAccountRoleAssignment, IAMRole)
        .join(IAMRole, IAMRole.id == ServiceAccountRoleAssignment.role_id)
        .where(ServiceAccountRoleAssignment.service_account_id == service_account_id)
        .order_by(IAMRole.name, ServiceAccountRoleAssignment.created_at)
    ).all()
    return [_role_assignment_response(assignment, role) for assignment, role in rows]


def add_role_assignment(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    payload: ServiceAccountRoleAssignmentRequest,
    actor_user_id: uuid.UUID,
    actor_authorization: AuthorizationContext,
) -> ServiceAccountRoleAssignment:
    account = _lock_service_account(db, service_account_id)
    _require_active(account)
    _require_revision(account, payload.expected_service_account_revision)
    role = db.scalar(
        select(IAMRole).where(IAMRole.id == payload.role_id).with_for_update(read=True)
    )
    if role is None:
        raise ServiceAccountRoleNotFound("Role not found.")
    if role.is_system:
        raise ServiceAccountSystemRoleRejected(
            "Built-in roles are sealed for human compatibility and cannot be "
            "assigned to service accounts. Create a bounded custom role instead."
        )
    if (
        payload.expected_role_revision is not None
        and role.revision != payload.expected_role_revision
    ):
        raise ServiceAccountRoleRevisionConflict(role)
    raw_permissions = set(
        db.scalars(
            select(IAMRolePermission.permission).where(
                IAMRolePermission.role_id == role.id
            )
        ).all()
    )
    if raw_permissions & WILDCARD_PERMISSION_IDS:
        raise ServiceAccountRoleContainsWildcard(
            "Roles assigned to service accounts must enumerate concrete permissions."
        )
    unsafe_permissions = sorted(raw_permissions - SAFE_SERVICE_ACCOUNT_PERMISSIONS)
    if unsafe_permissions:
        raise ServiceAccountRoleUnsafePermissions(unsafe_permissions)
    _require_service_account_delegation(actor_authorization, raw_permissions)
    existing = db.scalar(
        select(ServiceAccountRoleAssignment.id).where(
            ServiceAccountRoleAssignment.service_account_id == account.id,
            ServiceAccountRoleAssignment.role_id == role.id,
        )
    )
    if existing is not None:
        raise ServiceAccountRoleAssignmentConflict(
            "This role is already assigned to the service account."
        )
    assignment = ServiceAccountRoleAssignment(
        service_account_id=account.id,
        role_id=role.id,
        assigned_by_user_id=actor_user_id,
    )
    db.add(assignment)
    account.revision += 1
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) == "uq_service_account_role_assignments":
            raise ServiceAccountRoleAssignmentConflict(
                "This role assignment already exists or changed concurrently. Reload "
                "the service account and retry."
            ) from exc
        raise
    bump_iam_policy_revision(db)
    return assignment


def remove_role_assignment(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    assignment_id: uuid.UUID,
    expected_revision: int,
) -> ServiceAccountRoleAssignment:
    account = _lock_service_account(db, service_account_id)
    _require_revision(account, expected_revision)
    assignment = db.scalar(
        select(ServiceAccountRoleAssignment)
        .where(
            ServiceAccountRoleAssignment.id == assignment_id,
            ServiceAccountRoleAssignment.service_account_id == account.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise ServiceAccountRoleAssignmentNotFound("Role assignment not found.")
    role = db.scalar(
        select(IAMRole)
        .where(IAMRole.id == assignment.role_id)
        .with_for_update(read=True)
    )
    if role is None:
        raise ServiceAccountRoleNotFound(
            "The assigned role no longer exists. Repair the IAM data before retrying."
        )
    db.delete(assignment)
    account.revision += 1
    db.add(account)
    db.flush()
    bump_iam_policy_revision(db)
    return assignment


def list_credentials(
    db: Session,
    service_account_id: uuid.UUID,
    *,
    page: int,
    page_size: int,
) -> tuple[list[ServiceAccountCredentialResponse], int]:
    _require_service_account(db, service_account_id)
    criteria = (ServiceAccountCredential.service_account_id == service_account_id,)
    total = int(
        db.scalar(select(func.count(ServiceAccountCredential.id)).where(*criteria)) or 0
    )
    credentials = list(
        db.scalars(
            select(ServiceAccountCredential)
            .where(*criteria)
            .order_by(
                ServiceAccountCredential.created_at.desc(),
                ServiceAccountCredential.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return [_credential_response(credential) for credential in credentials], total


def issue_credential(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    payload: ServiceAccountCredentialIssueRequest,
    actor_user_id: uuid.UUID,
    actor_authorization: AuthorizationContext,
    idempotency_key: str,
    rotated_from_credential_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> ServiceAccountCredentialIssueResult:
    account = _lock_service_account(db, service_account_id)
    operation_key_hash = _idempotency_key_hash(idempotency_key)
    operation_request_hash = _credential_operation_request_hash(
        "issue", payload, source_credential_id=None
    )
    _reject_existing_operation(
        db,
        account=account,
        operation_kind="issue",
        operation_key_hash=operation_key_hash,
        operation_request_hash=operation_request_hash,
    )
    _require_active(account)
    _require_revision(account, payload.expected_service_account_revision)
    scopes = _validate_credential_scopes(db, account.id, payload.scopes)
    _require_service_account_delegation(actor_authorization, scopes)
    issued_at = now or datetime.now(timezone.utc)
    token, credential = _insert_unique_credential(
        db,
        service_account_id=account.id,
        actor_user_id=actor_user_id,
        name=payload.name,
        scopes=scopes,
        issued_at=issued_at,
        expires_at=_credential_expiry_at(issued_at, payload.expires_in_days),
        rotated_from_credential_id=rotated_from_credential_id,
        operation_kind="issue",
        operation_key_hash=operation_key_hash,
        operation_request_hash=operation_request_hash,
    )
    account.revision += 1
    db.add(account)
    db.flush()
    return ServiceAccountCredentialIssueResult(
        account=account, credential=credential, token=token
    )


def revoke_credential(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    credential_id: uuid.UUID,
    expected_revision: int,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[ServiceAccountCredential, ServiceAccountMutationResult]:
    account = _lock_service_account(db, service_account_id)
    credential = _lock_credential(db, account.id, credential_id)
    if credential.revoked_at is not None:
        return credential, ServiceAccountMutationResult(account=account, changed=False)
    _require_revision(account, expected_revision)
    credential.revoked_at = now or datetime.now(timezone.utc)
    credential.revoked_by_user_id = actor_user_id
    account.revision += 1
    db.add_all([account, credential])
    bump_iam_policy_revision(db)
    db.flush()
    return credential, ServiceAccountMutationResult(account=account, changed=True)


def rotate_credential(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    credential_id: uuid.UUID,
    payload: ServiceAccountCredentialIssueRequest,
    actor_user_id: uuid.UUID,
    actor_authorization: AuthorizationContext,
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceAccountCredentialIssueResult:
    account = _lock_service_account(db, service_account_id)
    old_credential = _lock_credential(db, account.id, credential_id)
    operation_key_hash = _idempotency_key_hash(idempotency_key)
    operation_request_hash = _credential_operation_request_hash(
        "rotate", payload, source_credential_id=old_credential.id
    )
    _reject_existing_operation(
        db,
        account=account,
        operation_kind="rotate",
        operation_key_hash=operation_key_hash,
        operation_request_hash=operation_request_hash,
    )
    _require_active(account)
    if old_credential.revoked_at is not None:
        raise ServiceAccountCredentialRevoked(
            "This credential has already been revoked and cannot be rotated. Create "
            "a new credential instead."
        )
    _require_revision(account, payload.expected_service_account_revision)
    scopes = _validate_credential_scopes(db, account.id, payload.scopes)
    _require_service_account_delegation(actor_authorization, scopes)
    rotated_at = now or datetime.now(timezone.utc)
    token, credential = _insert_unique_credential(
        db,
        service_account_id=account.id,
        actor_user_id=actor_user_id,
        name=payload.name,
        scopes=scopes,
        issued_at=rotated_at,
        expires_at=_credential_expiry_at(rotated_at, payload.expires_in_days),
        rotated_from_credential_id=old_credential.id,
        operation_kind="rotate",
        operation_key_hash=operation_key_hash,
        operation_request_hash=operation_request_hash,
    )
    overlap_deadline = rotated_at + SERVICE_ACCOUNT_ROTATION_OVERLAP
    old_expires_at = _as_utc(old_credential.expires_at)
    if old_expires_at > overlap_deadline:
        if old_credential.original_expires_at is None:
            old_credential.original_expires_at = old_credential.expires_at
        old_credential.expires_at = overlap_deadline
    account.revision += 1
    db.add_all([account, old_credential])
    bump_iam_policy_revision(db)
    db.flush()
    return ServiceAccountCredentialIssueResult(
        account=account,
        credential=credential,
        token=token,
        previous_credential_expires_at=_as_utc(old_credential.expires_at),
    )


def service_account_response(
    db: Session, account: ServiceAccount
) -> ServiceAccountResponse:
    role_ids = list(
        db.scalars(
            select(ServiceAccountRoleAssignment.role_id)
            .where(ServiceAccountRoleAssignment.service_account_id == account.id)
            .order_by(ServiceAccountRoleAssignment.role_id)
        ).all()
    )
    credential_count = int(
        db.scalar(
            select(func.count(ServiceAccountCredential.id)).where(
                ServiceAccountCredential.service_account_id == account.id
            )
        )
        or 0
    )
    now = datetime.now(timezone.utc)
    active_credential_count = int(
        db.scalar(
            select(func.count(ServiceAccountCredential.id)).where(
                ServiceAccountCredential.service_account_id == account.id,
                ServiceAccountCredential.revoked_at.is_(None),
                ServiceAccountCredential.expires_at > now,
            )
        )
        or 0
    )
    effective_permissions = (
        sorted(service_account_effective_permissions(db, account.id))
        if account.is_active
        else []
    )
    return ServiceAccountResponse(
        id=account.id,
        key=account.key,
        name=account.name,
        description=account.description,
        is_active=account.is_active,
        revision=account.revision,
        role_ids=role_ids,
        effective_permissions=effective_permissions,
        credential_count=credential_count,
        active_credential_count=active_credential_count,
        disabled_at=account.disabled_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _service_account_page_responses(
    db: Session,
    accounts: list[ServiceAccount],
) -> list[ServiceAccountResponse]:
    if not accounts:
        return []
    account_ids = [account.id for account in accounts]
    role_ids: dict[uuid.UUID, list[uuid.UUID]] = {
        account_id: [] for account_id in account_ids
    }
    for account_id, role_id in db.execute(
        select(
            ServiceAccountRoleAssignment.service_account_id,
            ServiceAccountRoleAssignment.role_id,
        )
        .where(ServiceAccountRoleAssignment.service_account_id.in_(account_ids))
        .order_by(
            ServiceAccountRoleAssignment.service_account_id,
            ServiceAccountRoleAssignment.role_id,
        )
    ):
        role_ids[account_id].append(role_id)

    credential_counts: dict[uuid.UUID, tuple[int, int]] = {
        account_id: (0, 0) for account_id in account_ids
    }
    now = datetime.now(timezone.utc)
    for account_id, total_count, active_count in db.execute(
        select(
            ServiceAccountCredential.service_account_id,
            func.count(ServiceAccountCredential.id),
            func.count(ServiceAccountCredential.id).filter(
                ServiceAccountCredential.revoked_at.is_(None),
                ServiceAccountCredential.expires_at > now,
            ),
        )
        .where(ServiceAccountCredential.service_account_id.in_(account_ids))
        .group_by(ServiceAccountCredential.service_account_id)
    ):
        credential_counts[account_id] = (int(total_count), int(active_count))

    raw_permissions: dict[uuid.UUID, set[str]] = {
        account_id: set() for account_id in account_ids
    }
    for account_id, permission in db.execute(
        select(
            ServiceAccountRoleAssignment.service_account_id,
            IAMRolePermission.permission,
        )
        .join(
            IAMRolePermission,
            IAMRolePermission.role_id == ServiceAccountRoleAssignment.role_id,
        )
        .where(ServiceAccountRoleAssignment.service_account_id.in_(account_ids))
    ):
        raw_permissions[account_id].add(permission)

    responses: list[ServiceAccountResponse] = []
    for account in accounts:
        total_count, active_count = credential_counts[account.id]
        effective_permissions = (
            sorted(
                expand_permission_grants(
                    raw_permissions[account.id] - WILDCARD_PERMISSION_IDS
                )
                & SAFE_SERVICE_ACCOUNT_PERMISSIONS
            )
            if account.is_active
            else []
        )
        responses.append(
            ServiceAccountResponse(
                id=account.id,
                key=account.key,
                name=account.name,
                description=account.description,
                is_active=account.is_active,
                revision=account.revision,
                role_ids=role_ids[account.id],
                effective_permissions=effective_permissions,
                credential_count=total_count,
                active_credential_count=active_count,
                disabled_at=account.disabled_at,
                created_at=account.created_at,
                updated_at=account.updated_at,
            )
        )
    return responses


def service_account_effective_permissions(
    db: Session, service_account_id: uuid.UUID
) -> frozenset[str]:
    grants = set(
        db.scalars(
            select(IAMRolePermission.permission)
            .join(
                ServiceAccountRoleAssignment,
                ServiceAccountRoleAssignment.role_id == IAMRolePermission.role_id,
            )
            .where(
                ServiceAccountRoleAssignment.service_account_id == service_account_id
            )
        ).all()
    )
    concrete_grants = grants - WILDCARD_PERMISSION_IDS
    return expand_permission_grants(concrete_grants) & SAFE_SERVICE_ACCOUNT_PERMISSIONS


def credential_response(
    credential: ServiceAccountCredential,
) -> ServiceAccountCredentialResponse:
    return _credential_response(credential)


def role_assignment_response(
    db: Session, assignment: ServiceAccountRoleAssignment
) -> ServiceAccountRoleAssignmentResponse:
    role = db.get(IAMRole, assignment.role_id)
    if role is None:
        raise ServiceAccountRoleNotFound(
            "The assigned role no longer exists. Repair the IAM data before retrying."
        )
    return _role_assignment_response(assignment, role)


def _require_service_account(
    db: Session, service_account_id: uuid.UUID
) -> ServiceAccount:
    account = db.get(ServiceAccount, service_account_id)
    if account is None:
        raise ServiceAccountNotFound("Service account not found.")
    return account


def _lock_service_account(db: Session, service_account_id: uuid.UUID) -> ServiceAccount:
    account = db.scalar(
        select(ServiceAccount)
        .where(ServiceAccount.id == service_account_id)
        .with_for_update()
    )
    if account is None:
        raise ServiceAccountNotFound("Service account not found.")
    return account


def _require_revision(account: ServiceAccount, expected_revision: int) -> None:
    if account.revision != expected_revision:
        raise ServiceAccountRevisionConflict(account)


def _require_active(account: ServiceAccount) -> None:
    if not account.is_active:
        raise ServiceAccountInactive(
            "The service account is disabled. Disabled principals cannot receive "
            "new roles or credentials."
        )


def _lock_credential(
    db: Session, service_account_id: uuid.UUID, credential_id: uuid.UUID
) -> ServiceAccountCredential:
    credential = db.scalar(
        select(ServiceAccountCredential)
        .where(
            ServiceAccountCredential.id == credential_id,
            ServiceAccountCredential.service_account_id == service_account_id,
        )
        .with_for_update()
    )
    if credential is None:
        raise ServiceAccountCredentialNotFound("Service-account credential not found.")
    return credential


def _validate_credential_scopes(
    db: Session, service_account_id: uuid.UUID, scopes: list[str]
) -> list[str]:
    normalized = sorted(set(scopes))
    disallowed = sorted(set(normalized) - SAFE_SERVICE_ACCOUNT_PERMISSIONS)
    if disallowed:
        raise ServiceAccountScopeNotAllowed(disallowed)
    effective = service_account_effective_permissions(db, service_account_id)
    missing = sorted(set(normalized) - effective)
    if missing:
        raise ServiceAccountScopeEscalation(missing)
    return normalized


def _require_service_account_delegation(
    authorization: AuthorizationContext,
    permissions: set[str] | list[str],
) -> None:
    try:
        require_delegable_permissions(authorization, permissions)
    except IAMDelegationDenied as exc:
        raise ServiceAccountDelegationDenied(exc.missing_permissions) from exc


def _insert_unique_credential(
    db: Session,
    *,
    service_account_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    name: str,
    scopes: list[str],
    issued_at: datetime,
    expires_at: datetime,
    rotated_from_credential_id: uuid.UUID | None,
    operation_kind: str | None = None,
    operation_key_hash: str | None = None,
    operation_request_hash: str | None = None,
) -> tuple[str, ServiceAccountCredential]:
    for _attempt in range(SERVICE_ACCOUNT_CREDENTIAL_GENERATION_ATTEMPTS):
        token, prefix, token_hash = _generate_service_account_token()
        credential = ServiceAccountCredential(
            service_account_id=service_account_id,
            rotated_from_credential_id=rotated_from_credential_id,
            name=name,
            token_prefix=prefix,
            token_hash=token_hash,
            operation_kind=operation_kind,
            operation_key_hash=operation_key_hash,
            operation_request_hash=operation_request_hash,
            scopes=scopes,
            expires_at=expires_at,
            created_by_user_id=actor_user_id,
            created_at=issued_at,
        )
        try:
            with db.begin_nested():
                db.add(credential)
                db.flush()
            return token, credential
        except IntegrityError as exc:
            constraint = _integrity_constraint_name(exc)
            if constraint in {
                "uq_service_account_credentials_prefix",
                "uq_service_account_credentials_hash",
            }:
                continue
            if constraint == "uq_service_account_credentials_operation_key":
                existing = db.scalar(
                    select(ServiceAccountCredential).where(
                        ServiceAccountCredential.service_account_id
                        == service_account_id,
                        ServiceAccountCredential.operation_key_hash
                        == operation_key_hash,
                    )
                )
                if existing is not None:
                    account = _require_service_account(db, service_account_id)
                    _raise_operation_replay(
                        existing,
                        account,
                        operation_kind=operation_kind,
                        operation_request_hash=operation_request_hash,
                    )
            raise
    raise ServiceAccountCredentialGenerationFailed(
        "A unique service-account credential could not be generated. Retry the request."
    )


def _credential_expiry_at(issued_at: datetime, expires_in_days: int) -> datetime:
    lifetime = timedelta(days=expires_in_days)
    if not (
        SERVICE_ACCOUNT_CREDENTIAL_MIN_LIFETIME
        <= lifetime
        <= SERVICE_ACCOUNT_CREDENTIAL_MAX_LIFETIME
    ):
        raise ServiceAccountConflict(
            "Credential expiry must be between 1 and 365 days."
        )
    return issued_at + lifetime


def _idempotency_key_hash(idempotency_key: str) -> str:
    normalized = idempotency_key.strip()
    if not 8 <= len(normalized) <= 200:
        raise ServiceAccountIdempotencyKeyInvalid(
            "Idempotency-Key must contain between 8 and 200 nonblank characters."
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _credential_operation_request_hash(
    operation_kind: str,
    payload: ServiceAccountCredentialIssueRequest,
    *,
    source_credential_id: uuid.UUID | None,
) -> str:
    canonical = json.dumps(
        {
            "expires_in_days": payload.expires_in_days,
            "name": payload.name,
            "operation": operation_kind,
            "scopes": sorted(set(payload.scopes)),
            "source_credential_id": (
                str(source_credential_id) if source_credential_id else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_existing_operation(
    db: Session,
    *,
    account: ServiceAccount,
    operation_kind: str,
    operation_key_hash: str,
    operation_request_hash: str,
) -> None:
    existing = db.scalar(
        select(ServiceAccountCredential).where(
            ServiceAccountCredential.service_account_id == account.id,
            ServiceAccountCredential.operation_key_hash == operation_key_hash,
        )
    )
    if existing is not None:
        _raise_operation_replay(
            existing,
            account,
            operation_kind=operation_kind,
            operation_request_hash=operation_request_hash,
        )


def _raise_operation_replay(
    credential: ServiceAccountCredential,
    account: ServiceAccount,
    *,
    operation_kind: str | None,
    operation_request_hash: str | None,
) -> None:
    if (
        credential.operation_kind != operation_kind
        or credential.operation_request_hash != operation_request_hash
    ):
        raise ServiceAccountIdempotencyConflict(
            "This Idempotency-Key was already used for a different credential "
            "operation or request payload. Retry with a new key."
        )
    if operation_kind == "rotate":
        raise ServiceAccountRotationAlreadyCommitted(credential, account)
    raise ServiceAccountCredentialIssueAlreadyCommitted(credential, account)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _generate_service_account_token() -> tuple[str, str, str]:
    public_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    prefix = f"{SERVICE_ACCOUNT_TOKEN_MARKER}_{public_id}"
    token = f"{prefix}_{secret}"
    return token, prefix, hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_service_account_token_prefix(token: str) -> str | None:
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    marker, public_id, secret = parts
    if marker != SERVICE_ACCOUNT_TOKEN_MARKER or not public_id or not secret:
        return None
    return f"{marker}_{public_id}"


def hash_service_account_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _credential_response(
    credential: ServiceAccountCredential,
) -> ServiceAccountCredentialResponse:
    return ServiceAccountCredentialResponse(
        id=credential.id,
        service_account_id=credential.service_account_id,
        rotated_from_credential_id=credential.rotated_from_credential_id,
        name=credential.name,
        token_prefix=credential.token_prefix,
        scopes=credential.scopes,
        expires_at=credential.expires_at,
        original_expires_at=credential.original_expires_at,
        revoked_at=credential.revoked_at,
        last_used_at=credential.last_used_at,
        last_used_ip=credential.last_used_ip,
        last_used_user_agent=credential.last_used_user_agent,
        created_at=credential.created_at,
    )


def _role_assignment_response(
    assignment: ServiceAccountRoleAssignment, role: IAMRole
) -> ServiceAccountRoleAssignmentResponse:
    return ServiceAccountRoleAssignmentResponse(
        id=assignment.id,
        service_account_id=assignment.service_account_id,
        role_id=role.id,
        role_key=role.key,
        role_name=role.name,
        role_revision=role.revision,
        created_at=assignment.created_at,
    )
