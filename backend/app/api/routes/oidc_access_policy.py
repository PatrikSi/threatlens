from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.api.routes.oidc_provider import authorize_oidc_provider_admin
from app.core.api_errors import ApiHTTPException
from app.core.logging_config import verbose_logging_enabled
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_IAM, SCOPE_WRITE_IAM
from app.db.session import get_db
from app.models.oidc import OIDCProvider
from app.models.user import User
from app.schemas.oidc_access import (
    OIDCAccessPolicyCreateRequest,
    OIDCAccessPolicyStateResponse,
    OIDCAccessPolicyUpdateRequest,
    OIDCClaimMappingSetCreateRequest,
    OIDCClaimMappingSetUpdateRequest,
)
from app.services.audit import record_audit
from app.services.auth_sessions import lock_user_auth_states
from app.services.authorization import lock_iam_policy_for_mutation
from app.services.oidc_access_admin import (
    OIDCAccessAdminError,
    OIDCAccessMutationResult,
    access_policy_state,
    create_access_policy,
    create_mapping_set,
    delete_access_policy,
    delete_mapping_set,
    update_access_policy,
    update_mapping_set,
)
from app.services.oidc_access_lifecycle import (
    oidc_access_affected_user_ids,
    provider_oidc_source_keys,
)
from app.services.oidc_config import (
    OIDC_PROVIDER_SYSTEM_KEY,
    load_primary_oidc_provider,
)
from app.services.user_access import acquire_oidc_provider_config_lock


router = APIRouter()
logger = logging.getLogger("threatlens.oidc.access")


@router.get("/access-policy", response_model=OIDCAccessPolicyStateResponse)
def get_oidc_access_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_IAM)),
):
    provider = load_primary_oidc_provider(db)
    return access_policy_state(db, provider.id if provider is not None else None)


@router.post(
    "/access-policy",
    response_model=OIDCAccessPolicyStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_oidc_access_policy(
    payload: OIDCAccessPolicyCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=False
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.access_policy.create",
        resource_type="oidc_access_policy",
        resource_id=None,
        operation=lambda: create_access_policy(
            db,
            provider_id=provider.id,
            payload=payload,
            actor_user_id=admin.id,
        ),
    )
    return _state(result)


@router.put("/access-policy", response_model=OIDCAccessPolicyStateResponse)
def put_oidc_access_policy(
    payload: OIDCAccessPolicyUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=True
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.access_policy.update",
        resource_type="oidc_access_policy",
        resource_id=None,
        operation=lambda: update_access_policy(
            db,
            provider_id=provider.id,
            payload=payload,
            actor_user_id=admin.id,
        ),
    )
    return _state(result)


@router.delete("/access-policy", response_model=OIDCAccessPolicyStateResponse)
def remove_oidc_access_policy(
    request: Request,
    expected_revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=True
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.access_policy.delete",
        resource_type="oidc_access_policy",
        resource_id=None,
        operation=lambda: delete_access_policy(
            db,
            provider_id=provider.id,
            expected_revision=expected_revision,
            actor_user_id=admin.id,
        ),
    )
    return OIDCAccessPolicyStateResponse(
        configured=False, provider_id=provider.id, policy=result.policy
    )


@router.post(
    "/access-policy/mapping-sets",
    response_model=OIDCAccessPolicyStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_oidc_mapping_set(
    payload: OIDCClaimMappingSetCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=False
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.mapping_set.create",
        resource_type="oidc_claim_mapping_set",
        resource_id=None,
        operation=lambda: create_mapping_set(
            db,
            provider_id=provider.id,
            payload=payload,
            actor_user_id=admin.id,
        ),
    )
    return _state(result)


@router.put(
    "/access-policy/mapping-sets/{mapping_set_id}",
    response_model=OIDCAccessPolicyStateResponse,
)
def put_oidc_mapping_set(
    mapping_set_id: uuid.UUID,
    payload: OIDCClaimMappingSetUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=True
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.mapping_set.update",
        resource_type="oidc_claim_mapping_set",
        resource_id=str(mapping_set_id),
        operation=lambda: update_mapping_set(
            db,
            provider_id=provider.id,
            mapping_set_id=mapping_set_id,
            payload=payload,
            actor_user_id=admin.id,
        ),
    )
    return _state(result)


@router.delete(
    "/access-policy/mapping-sets/{mapping_set_id}",
    response_model=OIDCAccessPolicyStateResponse,
)
def remove_oidc_mapping_set(
    mapping_set_id: uuid.UUID,
    request: Request,
    expected_revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_IAM)),
):
    provider, admin = _prepare_mutation(
        request, db=db, admin=admin, prelock_materialized_access=True
    )
    result = _execute_mutation(
        db,
        request=request,
        admin_id=admin.id,
        action="oidc.mapping_set.delete",
        resource_type="oidc_claim_mapping_set",
        resource_id=str(mapping_set_id),
        operation=lambda: delete_mapping_set(
            db,
            provider_id=provider.id,
            mapping_set_id=mapping_set_id,
            expected_revision=expected_revision,
            actor_user_id=admin.id,
        ),
    )
    return _state(result)


def _prepare_mutation(
    request: Request,
    *,
    db: Session,
    admin: User,
    prelock_materialized_access: bool,
) -> tuple[OIDCProvider, User]:
    lock_iam_policy_for_mutation(db)
    acquire_oidc_provider_config_lock(db)
    provider = db.scalar(
        select(OIDCProvider)
        .where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY)
        .with_for_update()
    )
    affected_user_ids: tuple[uuid.UUID, ...] = ()
    if provider is not None and prelock_materialized_access:
        role_keys, group_keys = provider_oidc_source_keys(db, provider.id)
        affected_user_ids = oidc_access_affected_user_ids(
            db,
            role_source_keys=role_keys,
            group_source_keys=group_keys,
        )
    locked_users = lock_user_auth_states(db, [admin.id, *affected_user_ids])
    locked_admin = authorize_oidc_provider_admin(
        request,
        db=db,
        admin=admin,
        locked_admin=locked_users.get(admin.id),
        action="oidc_access_policy_update",
        operation_label="OIDC access policy",
    )
    if provider is None:
        _record_rejection(
            db,
            request=request,
            admin_id=locked_admin.id,
            action="oidc.access_policy.update",
            resource_type="oidc_access_policy",
            resource_id=None,
            reason="oidc_provider_not_configured",
            context={},
        )
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Configure the OIDC provider before creating custom claim mappings.",
            error_code="oidc_provider_not_configured",
        )
    return provider, locked_admin


def _execute_mutation(
    db: Session,
    *,
    request: Request,
    admin_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    operation: Callable[[], OIDCAccessMutationResult],
) -> OIDCAccessMutationResult:
    try:
        result = operation()
    except OIDCAccessAdminError as exc:
        db.rollback()
        _record_rejection(
            db,
            request=request,
            admin_id=admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=exc.code,
            context=exc.context,
        )
        headers = (
            {"X-Current-Version": str(exc.current_revision)}
            if exc.current_revision is not None
            else None
        )
        raise ApiHTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            error_code=exc.code,
            error_context=exc.context or None,
            headers=headers,
        ) from exc

    metadata = _mutation_metadata(result)
    try:
        record_audit(
            db,
            actor_user_id=admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=_resolved_resource_id(resource_id, result),
            metadata=metadata,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "oidc_access_policy_audit_commit_failed action=%s actor_user_id=%s error_type=%s",
            action,
            admin_id,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )
        raise ApiHTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The OIDC access-policy commit outcome could not be confirmed. Reload "
                "the policy before deciding whether to retry the operation."
            ),
            error_code="oidc_access_policy_commit_unavailable",
        ) from exc
    return result


def _record_rejection(
    db: Session,
    *,
    request: Request,
    admin_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    reason: str,
    context: dict[str, object],
) -> None:
    try:
        record_audit(
            db,
            actor_user_id=admin_id,
            request_id=getattr(request.state, "request_id", None),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=False,
            metadata={"reason": reason, **context},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "oidc_access_policy_rejection_audit_failed action=%s actor_user_id=%s error_type=%s",
            action,
            admin_id,
            type(exc).__name__,
            exc_info=verbose_logging_enabled(get_settings()),
        )


def _mutation_metadata(result: OIDCAccessMutationResult) -> dict[str, object]:
    policy = result.policy
    mapping_set = result.mapping_set
    return {
        "affected_policy_id": (
            str(result.affected_policy_id)
            if result.affected_policy_id is not None
            else None
        ),
        "affected_mapping_set_id": (
            str(result.affected_mapping_set_id)
            if result.affected_mapping_set_id is not None
            else None
        ),
        "previous_policy_revision": result.previous_policy_revision,
        "previous_mapping_set_revision": result.previous_mapping_set_revision,
        "policy_generation": result.policy_generation,
        "policy_revision": policy.revision if policy is not None else None,
        "policy_enabled": policy.enabled if policy is not None else False,
        "mapping_set_count": len(policy.mapping_sets) if policy is not None else 0,
        "mapping_set_id": str(mapping_set.id) if mapping_set is not None else None,
        "mapping_set_key": mapping_set.key if mapping_set is not None else None,
        "mapping_set_revision": (
            mapping_set.revision if mapping_set is not None else None
        ),
        "mapping_set_enabled": (
            mapping_set.enabled if mapping_set is not None else None
        ),
        "role_mapping_count": (
            len(mapping_set.role_mappings) if mapping_set is not None else None
        ),
        "group_mapping_count": (
            len(mapping_set.group_mappings) if mapping_set is not None else None
        ),
        "purged_role_assignments": result.purged_role_assignments,
        "purged_group_memberships": result.purged_group_memberships,
        "affected_user_count": result.affected_user_count,
        "access_reduced_user_count": result.access_reduced_user_count,
        "revoked_api_tokens": result.revoked_api_tokens,
        "revoked_auth_sessions": result.revoked_auth_sessions,
        "cancelled_pending_mfa_enrollments": (result.cancelled_pending_mfa_enrollments),
        "cleared_investigation_assignments": (result.cleared_investigation_assignments),
        "iam_policy_revision": result.iam_policy_revision,
    }


def _state(result: OIDCAccessMutationResult) -> OIDCAccessPolicyStateResponse:
    return OIDCAccessPolicyStateResponse(
        configured=result.policy is not None,
        provider_id=result.policy.provider_id if result.policy is not None else None,
        policy=result.policy,
    )


def _resolved_resource_id(
    requested_resource_id: str | None, result: OIDCAccessMutationResult
) -> str | None:
    if requested_resource_id is not None:
        return requested_resource_id
    if result.affected_mapping_set_id is not None:
        return str(result.affected_mapping_set_id)
    if result.affected_policy_id is not None:
        return str(result.affected_policy_id)
    if result.mapping_set is not None:
        return str(result.mapping_set.id)
    if result.policy is not None:
        return str(result.policy.id)
    return None


__all__ = ["router"]
