from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ALL_PERMISSION_IDS
from app.core.token_scopes import (
    SCOPE_READ_AI,
    SCOPE_READ_IAM,
    SCOPE_READ_SERVICE_ACCOUNTS,
    SCOPE_WRITE_AI,
    SCOPE_WRITE_IAM,
    SCOPE_WRITE_SERVICE_ACCOUNTS,
)
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.iam import (
    IAMGroupRoleAssignment,
    IAMRole,
    IAMUserRoleAssignment,
)
from app.models.oidc_access import OIDCRoleClaimMapping
from app.models.service_account import (
    ServiceAccount,
    ServiceAccountCredential,
    ServiceAccountRoleAssignment,
)
from app.models.temporary_elevation import TemporaryElevation
from app.services.ai_provider_attempts import (
    AIProviderAttemptReconciliationError,
    reconcile_ai_provider_attempt,
)
from app.services.authorization import database_clock
from app.services.iam_roles import IAMRoleError, delete_role
from app.services.service_accounts import ServiceAccountError, disable_service_account


class EmptyActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


RegisteredTargetResolver = Callable[[Session, uuid.UUID, bool], object | None]
RegisteredTargetSnapshotter = Callable[[Session, object], dict[str, object]]
RegisteredExecutor = Callable[[Session, object, uuid.UUID], dict[str, object]]


@dataclass(frozen=True, slots=True)
class RegisteredActionDataPolicy:
    version: int
    target_kind: Literal["system_control_plane", "ai_task_run"]


@dataclass(frozen=True)
class RegisteredActionDefinition:
    key: str
    version: int
    label: str
    description: str
    target_type: str
    audit_action: str
    requester_permission: str
    approver_permission: str
    risk: str
    payload_model: type[BaseModel]
    resolve_target: RegisteredTargetResolver
    snapshot_target: RegisteredTargetSnapshotter
    execute: RegisteredExecutor
    target_data_policy: RegisteredActionDataPolicy

    @property
    def payload_fields(self) -> tuple[str, ...]:
        return tuple(self.payload_model.model_fields)


@dataclass(frozen=True)
class RegisteredActionTarget:
    target_id: str
    snapshot: dict[str, object]
    resource: object


@dataclass(frozen=True)
class RegisteredActionResolvedTarget:
    target_id: str
    resource: object


class RegisteredActionError(RuntimeError):
    code = "registered_action_error"
    status_code = 409

    def __init__(
        self, detail: str, *, context: dict[str, object] | None = None
    ) -> None:
        self.detail = detail
        self.context = context or {}
        super().__init__(detail)


class RegisteredActionUnknown(RegisteredActionError):
    code = "registered_action_unknown"
    status_code = 400


class RegisteredActionVersionUnavailable(RegisteredActionError):
    code = "action_definition_version_unavailable"
    status_code = 503


class RegisteredActionPayloadInvalid(RegisteredActionError):
    code = "registered_action_payload_invalid"
    status_code = 422


class RegisteredActionTargetNotFound(RegisteredActionError):
    code = "registered_action_target_not_found"
    status_code = 404


class RegisteredActionTargetInvalid(RegisteredActionError):
    code = "registered_action_target_invalid"
    status_code = 422


class RegisteredActionTargetConflict(RegisteredActionError):
    code = "action_approval_target_changed"
    status_code = 409


def _resolve_service_account(
    db: Session, target_id: uuid.UUID, lock: bool
) -> object | None:
    query = select(ServiceAccount).where(ServiceAccount.id == target_id)
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def _snapshot_service_account(db: Session, resource: object) -> dict[str, object]:
    account = _as_service_account(resource)
    credential_ids = sorted(
        str(value)
        for value in db.scalars(
            select(ServiceAccountCredential.id).where(
                ServiceAccountCredential.service_account_id == account.id,
                ServiceAccountCredential.revoked_at.is_(None),
            )
        ).all()
    )
    role_assignment_ids = sorted(
        str(value)
        for value in db.scalars(
            select(ServiceAccountRoleAssignment.id).where(
                ServiceAccountRoleAssignment.service_account_id == account.id
            )
        ).all()
    )
    return {
        "id": str(account.id),
        "key": account.key,
        "name": account.name,
        "revision": int(account.revision),
        "is_active": account.is_active,
        "precondition_digest": _precondition_digest(
            {
                "active_credential_ids": credential_ids,
                "role_assignment_ids": role_assignment_ids,
            }
        ),
    }


def _execute_service_account_disable(
    db: Session, resource: object, actor_user_id: uuid.UUID
) -> dict[str, object]:
    account = _as_service_account(resource)
    mutation = disable_service_account(
        db,
        service_account_id=account.id,
        expected_revision=account.revision,
        actor_user_id=actor_user_id,
    )
    return {
        "changed": mutation.changed,
        "new_revision": mutation.account.revision,
        "revoked_credentials": mutation.affected_count,
    }


def _resolve_iam_role(db: Session, target_id: uuid.UUID, lock: bool) -> object | None:
    query = select(IAMRole).where(IAMRole.id == target_id)
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def _snapshot_iam_role(db: Session, resource: object) -> dict[str, object]:
    role = _as_iam_role(resource)
    clock = database_clock(db)
    blockers = {
        "user_assignment_ids": sorted(
            str(value)
            for value in db.scalars(
                select(IAMUserRoleAssignment.id).where(
                    IAMUserRoleAssignment.role_id == role.id
                )
            ).all()
        ),
        "group_assignment_ids": sorted(
            str(value)
            for value in db.scalars(
                select(IAMGroupRoleAssignment.id).where(
                    IAMGroupRoleAssignment.role_id == role.id
                )
            ).all()
        ),
        "service_account_assignment_ids": sorted(
            str(value)
            for value in db.scalars(
                select(ServiceAccountRoleAssignment.id).where(
                    ServiceAccountRoleAssignment.role_id == role.id
                )
            ).all()
        ),
        "oidc_mapping_ids": sorted(
            str(value)
            for value in db.scalars(
                select(OIDCRoleClaimMapping.id).where(
                    OIDCRoleClaimMapping.role_id == role.id
                )
            ).all()
        ),
        "live_elevation_ids": sorted(
            str(value)
            for value in db.scalars(
                select(TemporaryElevation.id).where(
                    TemporaryElevation.role_id == role.id,
                    (
                        (
                            (TemporaryElevation.status == "pending")
                            & (TemporaryElevation.request_expires_at > clock)
                        )
                        | (
                            (TemporaryElevation.status == "approved")
                            & (TemporaryElevation.grant_expires_at > clock)
                        )
                    ),
                )
            ).all()
        ),
    }
    return {
        "id": str(role.id),
        "key": role.key,
        "name": role.name,
        "revision": int(role.revision),
        "is_system": role.is_system,
        "precondition_digest": _precondition_digest(blockers),
        "blocker_count": sum(len(values) for values in blockers.values()),
    }


def _execute_iam_role_delete(
    db: Session, resource: object, _actor_user_id: uuid.UUID
) -> dict[str, object]:
    role = _as_iam_role(resource)
    deleted = delete_role(
        db,
        role_id=role.id,
        expected_revision=role.revision,
    )
    return {
        "changed": True,
        "deleted_role_key": deleted.key,
        "deleted_role_name": deleted.name,
    }


def _resolve_ai_provider_attempt_receipt(
    db: Session, target_id: uuid.UUID, lock: bool
) -> object | None:
    query = select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.id == target_id
    )
    if not lock:
        return db.scalar(query.execution_options(populate_existing=True))

    # Provider reservation, approval capture, and history retention all use
    # run -> receipt locking. Read the immutable binding optimistically, lock
    # its run first, then lock and revalidate the receipt so a missing or
    # replaced target fails closed without introducing an opposing lock order.
    task_run_id = db.scalar(
        select(AIProviderAttemptReceipt.task_run_id_snapshot).where(
            AIProviderAttemptReceipt.id == target_id
        )
    )
    if task_run_id is None:
        return None
    run_lock = db.execute(
        select(AITaskRun.id)
        .where(AITaskRun.id == task_run_id)
        .with_for_update(read=True)
    )
    run_lock.close()
    receipt = db.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )
    if receipt is None or receipt.task_run_id_snapshot != task_run_id:
        return None
    return receipt


def _snapshot_ai_provider_attempt_receipt(
    _db: Session, resource: object
) -> dict[str, object]:
    receipt = _as_ai_provider_attempt_receipt(resource)
    evidence: dict[str, object] = {
        "id": str(receipt.id),
        "operation_id": str(receipt.operation_id),
        "attempt_number": int(receipt.attempt_number),
        "max_attempts": int(receipt.max_attempts),
        "reservation_generation": int(receipt.reservation_generation),
        "pre_io_failure_count": int(receipt.pre_io_failure_count),
        "last_pre_io_failure_at": (
            receipt.last_pre_io_failure_at.isoformat()
            if receipt.last_pre_io_failure_at is not None
            else None
        ),
        "request_fingerprint": receipt.request_fingerprint,
        "task_run_id_snapshot": str(receipt.task_run_id_snapshot),
        "feature_type": receipt.feature_type,
        "resource_type": receipt.resource_type,
        "resource_id": (
            str(receipt.resource_id) if receipt.resource_id is not None else None
        ),
        "requested_max_tokens": int(receipt.requested_max_tokens),
        "iam_revision": receipt.iam_revision,
        "data_policy_revision": receipt.data_policy_revision,
        "data_policy_mode": receipt.data_policy_mode,
        "state": receipt.state,
        "io_outcome": receipt.io_outcome,
        "retryable": receipt.retryable,
        "next_max_tokens": receipt.next_max_tokens,
        "created_at": receipt.created_at.isoformat(),
        "settled_at": (
            receipt.settled_at.isoformat()
            if receipt.settled_at is not None
            else None
        ),
        "reconciliation_action": receipt.reconciliation_action,
    }
    return {
        **evidence,
        "revision": int(receipt.revision),
        "precondition_digest": _precondition_digest(evidence),
    }


def _execute_ai_provider_attempt_confirm_not_sent(
    db: Session, resource: object, actor_user_id: uuid.UUID
) -> dict[str, object]:
    return _execute_ai_provider_attempt_reconciliation(
        db,
        resource=resource,
        actor_user_id=actor_user_id,
        action="confirmed_not_sent",
    )


def _execute_ai_provider_attempt_acknowledge_may_have_sent(
    db: Session, resource: object, actor_user_id: uuid.UUID
) -> dict[str, object]:
    return _execute_ai_provider_attempt_reconciliation(
        db,
        resource=resource,
        actor_user_id=actor_user_id,
        action="acknowledged_may_have_sent",
    )


def _execute_ai_provider_attempt_reconciliation(
    db: Session,
    *,
    resource: object,
    actor_user_id: uuid.UUID,
    action: str,
) -> dict[str, object]:
    receipt = _as_ai_provider_attempt_receipt(resource)
    reconciled = reconcile_ai_provider_attempt(
        db,
        receipt_id=receipt.id,
        expected_revision=receipt.revision,
        action=action,
        actor_user_id=actor_user_id,
    )
    return {
        "changed": True,
        "receipt_id": str(reconciled.id),
        "operation_id": str(reconciled.operation_id),
        "reconciliation_action": reconciled.reconciliation_action,
        "reconciled_from_state": reconciled.reconciled_from_state,
        "reconciled_from_io_outcome": reconciled.reconciled_from_io_outcome,
        "state": reconciled.state,
        "io_outcome": reconciled.io_outcome,
        "retryable": reconciled.retryable,
        "new_revision": int(reconciled.revision),
    }


ACTION_DEFINITIONS: tuple[RegisteredActionDefinition, ...] = (
    RegisteredActionDefinition(
        key="ai.provider_attempt.confirm_not_sent",
        version=1,
        label="Confirm AI provider attempt was not sent",
        description=(
            "Resolve one unsafe AI provider receipt after independent evidence "
            "confirms the request did not reach the provider. The current logical "
            "operation can continue only when its durable retry budget remains."
        ),
        target_type="ai_provider_attempt_receipt",
        audit_action="ai.provider_attempt.confirm_not_sent",
        requester_permission=SCOPE_READ_AI,
        approver_permission=SCOPE_WRITE_AI,
        risk="critical",
        payload_model=EmptyActionPayload,
        resolve_target=_resolve_ai_provider_attempt_receipt,
        snapshot_target=_snapshot_ai_provider_attempt_receipt,
        execute=_execute_ai_provider_attempt_confirm_not_sent,
        target_data_policy=RegisteredActionDataPolicy(
            version=1,
            target_kind="ai_task_run",
        ),
    ),
    RegisteredActionDefinition(
        key="ai.provider_attempt.acknowledge_may_have_sent",
        version=1,
        label="Acknowledge AI provider attempt may have been sent",
        description=(
            "Resolve one unsafe AI provider receipt after independently accepting "
            "that the provider may have processed the request."
        ),
        target_type="ai_provider_attempt_receipt",
        audit_action="ai.provider_attempt.acknowledge_may_have_sent",
        requester_permission=SCOPE_READ_AI,
        approver_permission=SCOPE_WRITE_AI,
        risk="critical",
        payload_model=EmptyActionPayload,
        resolve_target=_resolve_ai_provider_attempt_receipt,
        snapshot_target=_snapshot_ai_provider_attempt_receipt,
        execute=_execute_ai_provider_attempt_acknowledge_may_have_sent,
        target_data_policy=RegisteredActionDataPolicy(
            version=1,
            target_kind="ai_task_run",
        ),
    ),
    RegisteredActionDefinition(
        key="service_account.disable",
        version=1,
        label="Disable service account",
        description="Disable one service account and revoke all of its active credentials.",
        target_type="service_account",
        audit_action="service_accounts.disable",
        requester_permission=SCOPE_READ_SERVICE_ACCOUNTS,
        approver_permission=SCOPE_WRITE_SERVICE_ACCOUNTS,
        risk="critical",
        payload_model=EmptyActionPayload,
        resolve_target=_resolve_service_account,
        snapshot_target=_snapshot_service_account,
        execute=_execute_service_account_disable,
        target_data_policy=RegisteredActionDataPolicy(
            version=1,
            target_kind="system_control_plane",
        ),
    ),
    RegisteredActionDefinition(
        key="iam.role.delete",
        version=1,
        label="Delete custom role",
        description="Delete one unassigned custom IAM role.",
        target_type="iam_role",
        audit_action="iam.roles.delete",
        requester_permission=SCOPE_READ_IAM,
        approver_permission=SCOPE_WRITE_IAM,
        risk="critical",
        payload_model=EmptyActionPayload,
        resolve_target=_resolve_iam_role,
        snapshot_target=_snapshot_iam_role,
        execute=_execute_iam_role_delete,
        target_data_policy=RegisteredActionDataPolicy(
            version=1,
            target_kind="system_control_plane",
        ),
    ),
)
ACTION_DEFINITION_BY_KEY_VERSION = {
    (definition.key, definition.version): definition
    for definition in ACTION_DEFINITIONS
}
LATEST_ACTION_DEFINITION_BY_KEY: dict[str, RegisteredActionDefinition] = {}
for _definition in ACTION_DEFINITIONS:
    current = LATEST_ACTION_DEFINITION_BY_KEY.get(_definition.key)
    if current is None or _definition.version > current.version:
        LATEST_ACTION_DEFINITION_BY_KEY[_definition.key] = _definition


def validate_action_registry() -> None:
    if len(ACTION_DEFINITION_BY_KEY_VERSION) != len(ACTION_DEFINITIONS):
        raise RuntimeError("Registered action key/version pairs must be unique.")
    for definition in ACTION_DEFINITIONS:
        for permission in (
            definition.requester_permission,
            definition.approver_permission,
        ):
            if permission not in ALL_PERMISSION_IDS:
                raise RuntimeError(
                    f"Registered action {definition.key}@{definition.version} uses unknown permission {permission}."
                )
        if definition.version < 1:
            raise RuntimeError("Registered action versions must be positive integers.")
        policy = definition.target_data_policy
        if not isinstance(policy, RegisteredActionDataPolicy):
            raise RuntimeError(
                f"Registered action {definition.key}@{definition.version} is "
                "missing its target data-policy declaration."
            )
        if policy.version < 1:
            raise RuntimeError(
                "Registered action target data-policy versions must be positive integers."
            )
        expected_target_types = {
            "system_control_plane": {"service_account", "iam_role"},
            "ai_task_run": {"ai_provider_attempt_receipt"},
        }
        expected_target_type = expected_target_types.get(policy.target_kind)
        if (
            expected_target_type is None
            or definition.target_type not in expected_target_type
        ):
            raise RuntimeError(
                f"Registered action {definition.key}@{definition.version} has an "
                "incompatible target data-policy declaration."
            )


validate_action_registry()


def get_registered_action(
    action_type: str,
    *,
    version: int | None = None,
) -> RegisteredActionDefinition:
    if version is None:
        definition = LATEST_ACTION_DEFINITION_BY_KEY.get(action_type)
        if definition is None:
            raise RegisteredActionUnknown(
                "This action type is not registered for approval-backed execution.",
                context={"action_type": action_type},
            )
        return definition
    definition = ACTION_DEFINITION_BY_KEY_VERSION.get((action_type, version))
    if definition is None:
        raise RegisteredActionVersionUnavailable(
            "The exact registered action version required by this approval is unavailable. Upgrade or restore the matching application version before retrying.",
            context={"action_type": action_type, "definition_version": version},
        )
    return definition


def normalize_registered_action_payload(
    definition: RegisteredActionDefinition,
    payload: dict[str, object],
) -> dict[str, object]:
    try:
        normalized = definition.payload_model.model_validate(payload)
    except ValidationError as exc:
        errors = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "type": error["type"],
            }
            for error in exc.errors(include_input=False, include_url=False)[:20]
        ]
        raise RegisteredActionPayloadInvalid(
            "The action payload does not match the registered schema.",
            context={"validation_errors": errors},
        ) from exc
    return normalized.model_dump(mode="json")


def inspect_registered_action_target(
    db: Session,
    *,
    definition: RegisteredActionDefinition,
    target_id: str,
    target_revision: int,
    lock: bool = False,
) -> RegisteredActionTarget:
    target = resolve_registered_action_target(
        db,
        definition=definition,
        target_id=target_id,
        lock=lock,
    )
    if target is None:
        parsed_id = _parse_target_uuid(target_id, definition.target_type)
        raise RegisteredActionTargetNotFound(
            f"The {definition.target_type.replace('_', ' ')} target was not found.",
            context={
                "target_type": definition.target_type,
                "target_id": str(parsed_id),
            },
        )
    return inspect_resolved_registered_action_target(
        db,
        definition=definition,
        target=target,
        target_revision=target_revision,
    )


def resolve_registered_action_target(
    db: Session,
    *,
    definition: RegisteredActionDefinition,
    target_id: str,
    lock: bool = False,
) -> RegisteredActionResolvedTarget | None:
    parsed_id = _parse_target_uuid(target_id, definition.target_type)
    resource = definition.resolve_target(db, parsed_id, lock)
    if resource is None:
        return None
    return RegisteredActionResolvedTarget(
        target_id=str(parsed_id),
        resource=resource,
    )


def inspect_resolved_registered_action_target(
    db: Session,
    *,
    definition: RegisteredActionDefinition,
    target: RegisteredActionResolvedTarget,
    target_revision: int,
) -> RegisteredActionTarget:
    resource = target.resource
    parsed_id = uuid.UUID(target.target_id)
    snapshot = definition.snapshot_target(db, resource)
    current_revision = int(snapshot["revision"])
    if current_revision != target_revision:
        raise RegisteredActionTargetConflict(
            "The action target changed after it was selected. Reload it and create a new approval request.",
            context={
                "target_type": definition.target_type,
                "target_id": str(parsed_id),
                "expected_revision": target_revision,
                "current_revision": current_revision,
            },
        )
    if isinstance(resource, ServiceAccount) and not resource.is_active:
        raise RegisteredActionTargetConflict(
            "The service account is already disabled; no approval-backed action is required.",
            context={"target_id": str(parsed_id), "current_revision": current_revision},
        )
    if isinstance(resource, IAMRole):
        if resource.is_system:
            raise RegisteredActionTargetConflict(
                "Built-in roles are sealed and cannot be deleted.",
                context={
                    "target_id": str(parsed_id),
                    "current_revision": current_revision,
                },
            )
        if int(snapshot.get("blocker_count", 0)):
            raise RegisteredActionTargetConflict(
                "The custom role is still referenced and cannot be approved for deletion.",
                context={
                    "target_id": str(parsed_id),
                    "current_revision": current_revision,
                    "blocker_count": int(snapshot["blocker_count"]),
                },
            )
    if isinstance(resource, AIProviderAttemptReceipt):
        if (
            resource.reconciliation_action is not None
            or (resource.state, resource.io_outcome)
            not in {("reserved", "reserved"), ("ambiguous", "ambiguous")}
        ):
            raise RegisteredActionTargetConflict(
                "Only an unresolved reserved or ambiguous AI provider receipt "
                "can be reconciled.",
                context={
                    "target_id": str(parsed_id),
                    "current_revision": current_revision,
                    "state": resource.state,
                    "io_outcome": resource.io_outcome,
                },
            )
    return RegisteredActionTarget(
        target_id=str(parsed_id),
        snapshot=snapshot,
        resource=resource,
    )


def execute_registered_action(
    db: Session,
    *,
    definition: RegisteredActionDefinition,
    target_id: str,
    target_revision: int,
    expected_target_snapshot: dict[str, object],
    payload: dict[str, object],
    actor_user_id: uuid.UUID,
) -> dict[str, object]:
    normalize_registered_action_payload(definition, payload)
    target = inspect_registered_action_target(
        db,
        definition=definition,
        target_id=target_id,
        target_revision=target_revision,
        lock=True,
    )
    if target.snapshot.get("precondition_digest") != expected_target_snapshot.get(
        "precondition_digest"
    ):
        raise RegisteredActionTargetConflict(
            "The action target's execution preconditions changed after approval. No action was executed.",
            context={
                "target_type": definition.target_type,
                "target_id": target.target_id,
            },
        )
    try:
        return definition.execute(db, target.resource, actor_user_id)
    except (
        AIProviderAttemptReconciliationError,
        IAMRoleError,
        ServiceAccountError,
    ) as exc:
        raise RegisteredActionTargetConflict(str(exc)) from exc


def _parse_target_uuid(target_id: str, target_type: str) -> uuid.UUID:
    try:
        return uuid.UUID(target_id)
    except (TypeError, ValueError) as exc:
        raise RegisteredActionTargetInvalid(
            f"The {target_type.replace('_', ' ')} target ID must be a UUID.",
            context={"target_type": target_type},
        ) from exc


def _precondition_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _as_service_account(resource: object) -> ServiceAccount:
    if not isinstance(resource, ServiceAccount):
        raise RuntimeError(
            "Registered service-account action resolved the wrong target type."
        )
    return resource


def _as_iam_role(resource: object) -> IAMRole:
    if not isinstance(resource, IAMRole):
        raise RuntimeError("Registered IAM action resolved the wrong target type.")
    return resource


def _as_ai_provider_attempt_receipt(
    resource: object,
) -> AIProviderAttemptReceipt:
    if not isinstance(resource, AIProviderAttemptReceipt):
        raise RuntimeError(
            "Registered AI provider-attempt action resolved the wrong target type."
        )
    return resource


__all__ = [
    "ACTION_DEFINITIONS",
    "RegisteredActionDefinition",
    "RegisteredActionDataPolicy",
    "RegisteredActionError",
    "RegisteredActionPayloadInvalid",
    "RegisteredActionResolvedTarget",
    "RegisteredActionTarget",
    "RegisteredActionTargetConflict",
    "RegisteredActionTargetInvalid",
    "RegisteredActionTargetNotFound",
    "RegisteredActionUnknown",
    "RegisteredActionVersionUnavailable",
    "execute_registered_action",
    "get_registered_action",
    "inspect_registered_action_target",
    "inspect_resolved_registered_action_target",
    "normalize_registered_action_payload",
    "resolve_registered_action_target",
    "validate_action_registry",
]
