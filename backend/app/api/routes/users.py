import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_admin_user,
    get_current_auth_session_id,
    is_cookie_session_auth,
    require_token_scopes,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.rbac import ALL_ROLES, ROLE_ADMIN
from app.core.security import clear_auth_cookies, get_password_hash
from app.core.token_scopes import SCOPE_READ_USERS, SCOPE_WRITE_USERS
from app.db.session import get_db
from app.models.user import PROVISIONING_SOURCE_LOCAL, PROVISIONING_SOURCE_OIDC, User
from app.schemas.auth_security import AdminMFAResetRequest, AdminMFAResetResponse
from app.schemas.user import (
    UserAdminResponse,
    UserCreateRequest,
    UserDirectoryResponse,
    UserUpdateRequest,
)
from app.services.audit import record_audit
from app.services.auth_sessions import lock_exact_auth_session, lock_user_auth_states
from app.services.local_mfa import (
    MFAError,
    MFAInvalidCodeError,
    disable_totp,
    mfa_status,
)
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.investigation_ownership import (
    InvestigationOwnerReassignmentRequired,
    reconcile_user_investigation_access_change,
)
from app.services.password_verification import verify_current_password_or_raise
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_error_context,
)
from app.services.user_access import (
    LastActiveAdminError,
    LocalBreakGlassAdminRequiredError,
    acquire_active_admin_invariant_lock,
    ensure_active_approved_admin_remains,
    ensure_local_break_glass_admin_remains_when_oidc_disabled,
    load_user_for_access_update,
    lock_users_for_security_change,
    revoke_user_credentials_with_counts,
)
from app.services.user_directory import (
    UserManagementContext,
    load_user_management_context,
    load_user_management_contexts,
    user_directory_search_filter,
)

router = APIRouter(prefix="/users", tags=["users"])
logger = logging.getLogger("threatlens.users")


def _acquire_active_admin_invariant_lock(db: Session) -> None:
    acquire_active_admin_invariant_lock(db)


def _reload_admin_after_invariant_lock(db: Session, admin_id: uuid.UUID) -> User:
    admin = load_user_for_access_update(db, admin_id)
    if (
        admin is None
        or admin.role != ROLE_ADMIN
        or not admin.is_active
        or not admin.is_approved
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    return admin


def _ensure_active_approved_admin_remains(
    db: Session, user: User, payload: UserUpdateRequest
) -> None:
    next_role = payload.role if payload.role is not None else user.role
    next_is_active = (
        payload.is_active if payload.is_active is not None else user.is_active
    )
    next_is_approved = (
        payload.is_approved if payload.is_approved is not None else user.is_approved
    )

    try:
        ensure_active_approved_admin_remains(
            db,
            user,
            next_role=next_role,
            next_is_active=next_is_active,
            next_is_approved=next_is_approved,
        )
        ensure_local_break_glass_admin_remains_when_oidc_disabled(
            db,
            user,
            next_role=next_role,
            next_is_active=next_is_active,
            next_is_approved=next_is_approved,
            next_password_login_enabled=user.password_login_enabled,
        )
    except LastActiveAdminError as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code="last_active_admin",
            error_context={"user_id": str(user.id)},
        ) from exc
    except LocalBreakGlassAdminRequiredError as exc:
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code="oidc_break_glass_admin_required",
            error_context={"user_id": str(user.id)},
        ) from exc


@router.get("", response_model=list[UserAdminResponse])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    _ = admin
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    contexts = load_user_management_contexts(db, [user.id for user in users])
    return [
        _user_admin_response(user, contexts.get(user.id, UserManagementContext()))
        for user in users
    ]


@router.get("/directory", response_model=UserDirectoryResponse)
def list_user_directory(
    q: str | None = Query(default=None, max_length=200),
    role: str | None = Query(default=None),
    provisioning_source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=250),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    _ = admin
    if role is not None and role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user-directory role filter",
        )
    if provisioning_source is not None and provisioning_source not in {
        PROVISIONING_SOURCE_LOCAL,
        PROVISIONING_SOURCE_OIDC,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user-directory provisioning source filter",
        )

    filters = []
    normalized_query = (q or "").strip().lower()
    if normalized_query:
        filters.append(user_directory_search_filter(normalized_query))
    if role is not None:
        filters.append(User.role == role)
    if provisioning_source is not None:
        filters.append(User.provisioning_source == provisioning_source)

    total = int(db.scalar(select(func.count(User.id)).where(*filters)) or 0)
    users = list(
        db.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.asc(), User.id.asc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    contexts = load_user_management_contexts(db, [user.id for user in users])
    return UserDirectoryResponse(
        users=[
            _user_admin_response(
                user,
                contexts.get(user.id, UserManagementContext()),
            )
            for user in users
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(users) < total,
    )


@router.get("/{user_id}", response_model=UserAdminResponse)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_USERS)),
):
    _ = admin
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return _user_admin_response(
        user,
        load_user_management_context(db, user.id),
    )


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    if payload.role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role"
        )

    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        )

    user = User(
        email=payload.email.lower(),
        password_hash=get_password_hash(payload.password),
        provisioning_source=PROVISIONING_SOURCE_LOCAL,
        role=payload.role,
        is_active=payload.is_active,
        is_approved=payload.is_approved,
        approved_at=datetime.now(timezone.utc) if payload.is_approved else None,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        ) from exc
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.create",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "email": user.email,
            "role": user.role,
            "is_approved": user.is_approved,
            "provisioning_source": user.provisioning_source,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        ) from exc
    db.refresh(user)
    return _user_admin_response(user, UserManagementContext())


@router.patch(
    "/{user_id}",
    response_model=UserAdminResponse,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": (
                "Includes `user_security_version_conflict`; the current version is returned "
                "in `X-Current-Security-Version`."
            )
        },
    },
)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    access_state_update = any(
        value is not None
        for value in (payload.role, payload.is_active, payload.is_approved)
    )
    locked_users = (
        lock_users_for_security_change(db, [admin.id, user_id])
        if access_state_update
        else lock_user_auth_states(db, [admin.id, user_id])
    )
    admin = locked_users.get(admin.id)
    if (
        admin is None
        or admin.role != ROLE_ADMIN
        or not admin.is_active
        or not admin.is_approved
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    user = locked_users.get(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if payload.role is not None and payload.role not in ALL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role"
        )

    management = load_user_management_context(db, user.id)
    _ensure_locally_managed_changes(user, payload, management)

    email_before_update = user.email
    normalized_email = payload.email.lower() if payload.email is not None else None
    email_changed = normalized_email is not None and normalized_email != user.email
    current_security_version = int(user.auth_token_version or 0)
    legacy_unversioned_security_update = (
        access_state_update or email_changed
    ) and payload.expected_security_version is None
    if (
        payload.expected_security_version is not None
        and payload.expected_security_version != current_security_version
    ):
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This user changed after the directory entry was loaded. Reload the user "
                "and apply the intended changes again."
            ),
            error_code="user_security_version_conflict",
            error_context={
                "user_id": str(user.id),
                "current_security_version": current_security_version,
            },
            headers={"X-Current-Security-Version": str(current_security_version)},
        )

    try:
        _ensure_active_approved_admin_remains(db, user, payload)
    except ApiHTTPException as exc:
        actor_user_id = admin.id
        target_user_id = user.id
        rejection_reason = exc.error_code
        db.rollback()
        record_audit(
            db,
            actor_user_id=actor_user_id,
            action="users.update",
            resource_type="user",
            resource_id=str(target_user_id),
            success=False,
            metadata={"reason": rejection_reason},
        )
        db.commit()
        raise
    next_role = payload.role if payload.role is not None else user.role
    next_is_active = (
        payload.is_active if payload.is_active is not None else user.is_active
    )
    next_is_approved = (
        payload.is_approved if payload.is_approved is not None else user.is_approved
    )
    try:
        investigation_access = reconcile_user_investigation_access_change(
            db,
            user=user,
            next_role=next_role,
            next_is_active=next_is_active,
            next_is_approved=next_is_approved,
            actor_user_id=admin.id,
        )
    except InvestigationOwnerReassignmentRequired as exc:
        actor_user_id = admin.id
        target_user_id = user.id
        affected_count = len(exc.investigations)
        db.rollback()
        record_audit(
            db,
            actor_user_id=actor_user_id,
            action="users.update",
            resource_type="user",
            resource_id=str(target_user_id),
            success=False,
            metadata={
                "reason": "investigation_owner_reassignment_required",
                "affected_investigation_count": affected_count,
            },
        )
        db.commit()
        raise ApiHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            error_code="investigation_owner_reassignment_required",
            error_context={"affected_investigation_count": affected_count},
        ) from exc
    should_rotate_auth_tokens = payload.password is not None or email_changed
    revoked_api_tokens = 0
    revoked_auth_sessions = 0

    if payload.role is not None:
        if payload.role != user.role:
            should_rotate_auth_tokens = True
        user.role = payload.role

    if normalized_email is not None:
        existing = db.scalar(
            select(User).where(User.email == normalized_email, User.id != user_id)
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
            )
        user.email = normalized_email

    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            should_rotate_auth_tokens = True
        user.is_active = payload.is_active

    if payload.is_approved is not None:
        if payload.is_approved != user.is_approved:
            should_rotate_auth_tokens = True
        user.is_approved = payload.is_approved
        user.approved_at = datetime.now(timezone.utc) if payload.is_approved else None

    if payload.password is not None:
        user.password_hash = get_password_hash(payload.password)
        user.password_login_enabled = True

    if should_rotate_auth_tokens:
        revoked = revoke_user_credentials_with_counts(db, user)
        revoked_api_tokens = revoked.api_tokens
        revoked_auth_sessions = revoked.auth_sessions

    db.add(user)
    legacy_unversioned_password_update = (
        payload.password is not None and payload.expected_security_version is None
    )
    if legacy_unversioned_password_update:
        record_audit(
            db,
            actor_user_id=admin.id,
            action="users.compatibility.unversioned_password_update",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"security_version_before_update": current_security_version},
        )
    if legacy_unversioned_security_update:
        record_audit(
            db,
            actor_user_id=admin.id,
            action="users.compatibility.unversioned_security_update",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"security_version_before_update": current_security_version},
        )
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.update",
        resource_type="user",
        resource_id=str(user.id),
        metadata={
            "role": user.role,
            "is_active": user.is_active,
            "is_approved": user.is_approved,
            "email_updated": email_changed,
            "email_before_update": email_before_update if email_changed else None,
            "email_after_update": user.email if email_changed else None,
            "password_updated": payload.password is not None,
            "password_login_enabled": user.password_login_enabled,
            "auth_token_version": user.auth_token_version,
            "revoked_api_tokens": int(revoked_api_tokens),
            "revoked_auth_sessions": int(revoked_auth_sessions),
            "cleared_investigation_assignments": investigation_access.cleared_assignment_count,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use"
        ) from exc
    db.refresh(user)
    if legacy_unversioned_password_update:
        logger.warning(
            "legacy_unversioned_password_update actor_user_id=%s target_user_id=%s",
            admin.id,
            user.id,
        )
    if legacy_unversioned_security_update:
        logger.warning(
            "legacy_unversioned_security_update_accepted actor_user_id=%s "
            "target_user_id=%s",
            admin.id,
            user.id,
        )
    return _user_admin_response(
        user,
        load_user_management_context(db, user.id),
        credentials_rotated=should_rotate_auth_tokens,
        revoked_api_tokens=int(revoked_api_tokens),
        revoked_auth_sessions=int(revoked_auth_sessions),
    )


@router.post("/{user_id}/mfa/reset", response_model=AdminMFAResetResponse)
def reset_user_mfa(
    user_id: uuid.UUID,
    payload: AdminMFAResetRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_USERS)),
):
    _ = _scope_user
    if not is_cookie_session_auth(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA recovery requires an authenticated administrator browser session.",
        )
    locked_users = lock_user_auth_states(db, [admin.id, user_id])
    admin = locked_users.get(admin.id)
    if (
        admin is None
        or admin.role != ROLE_ADMIN
        or not admin.is_active
        or not admin.is_approved
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access changed. Sign in again before retrying MFA recovery.",
        )
    target = locked_users.get(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if (
        target.provisioning_source == PROVISIONING_SOURCE_OIDC
        or not target.password_login_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA for this SSO-managed account must be reset at the identity provider.",
        )
    target_mfa_enabled, _target_confirmed_at, _target_remaining = mfa_status(
        db, user_id=target.id
    )
    if not target_mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authenticator app verification is not enabled for this account.",
        )
    admin_auth_method = _verify_admin_recent_auth(
        db, request=request, admin=admin, current_password=payload.current_password
    )

    admin_mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=admin.id)
    if admin_auth_method == "local":
        if not admin_mfa_enabled:
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Administrator MFA recovery requires authenticator verification. "
                    "Enable local MFA before resetting another account."
                ),
                error_code="admin_mfa_assurance_required",
            )
        if not payload.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter your own authenticator or recovery code to reset another account's MFA.",
            )
        try:
            verify_sensitive_mfa_code(
                db,
                user=admin,
                code=payload.code,
                client_ip=resolve_client_ip(request),
            )
        except MFASensitiveActionRateLimitError as exc:
            _record_admin_mfa_reset_failure(
                db,
                admin=admin,
                target_user_id=user_id,
                reason="rate_limited",
            )
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds
                else None
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
                headers=headers,
            ) from exc
        except MFAInvalidCodeError as exc:
            _record_admin_mfa_reset_failure(
                db,
                admin=admin,
                target_user_id=user_id,
                reason="invalid_code",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except MFASensitiveActionThrottleUnavailableError as exc:
            _record_admin_mfa_reset_failure(
                db,
                admin=admin,
                target_user_id=user_id,
                reason="throttle_unavailable",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Shared MFA verification throttling is temporarily unavailable. "
                    "No administrator MFA code was checked; try again shortly."
                ),
                headers={"Retry-After": "5"},
            ) from exc
        except MFAError as exc:
            _record_admin_mfa_reset_failure(
                db,
                admin=admin,
                target_user_id=user_id,
                reason="verification_unavailable",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Administrator MFA verification is temporarily unavailable.",
            ) from exc

    disabled = disable_totp(db, user_id=target.id)
    revoked = revoke_user_credentials_with_counts(db, target)
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.mfa.reset",
        resource_type="user",
        resource_id=str(target.id),
        metadata={
            "reason": payload.reason.strip(),
            "disabled": disabled,
            "revoked_api_tokens": revoked.api_tokens,
            "revoked_auth_sessions": revoked.auth_sessions,
        },
    )
    db.commit()
    if target.id == admin.id:
        clear_auth_cookies(response)
    return AdminMFAResetResponse(
        disabled=disabled,
        revoked_api_tokens=revoked.api_tokens,
        revoked_auth_sessions=revoked.auth_sessions,
    )


def _ensure_locally_managed_changes(
    user: User,
    payload: UserUpdateRequest,
    management: UserManagementContext,
) -> None:
    provider_name = (
        management.provider.name
        if management.provider is not None
        else "the identity provider"
    )
    if (
        payload.password is not None
        and user.provisioning_source == PROVISIONING_SOURCE_OIDC
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Password is managed by {provider_name} for this SSO-provisioned account",
        )
    if (
        payload.email is not None
        and payload.email.lower() != user.email
        and user.provisioning_source == PROVISIONING_SOURCE_OIDC
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email is managed by {provider_name} for this SSO-provisioned account",
        )
    if (
        payload.role is not None
        and payload.role != user.role
        and management.role_managed_by == "oidc"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role is managed by {provider_name} and synchronized during SSO sign-in",
        )


def _user_admin_response(
    user: User,
    management: UserManagementContext,
    *,
    credentials_rotated: bool = False,
    revoked_api_tokens: int = 0,
    revoked_auth_sessions: int = 0,
) -> UserAdminResponse:
    identity = management.identity
    return UserAdminResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_approved=user.is_approved,
        approved_at=user.approved_at,
        created_at=user.created_at,
        password_login_enabled=user.password_login_enabled,
        provisioning_source=user.provisioning_source,
        authentication_methods=management.authentication_methods(user),
        oidc_provider_name=management.provider.name
        if management.provider is not None
        else None,
        oidc_linked_at=identity.created_at if identity is not None else None,
        oidc_last_login_at=identity.last_login_at if identity is not None else None,
        identity_linked=identity is not None,
        sso_sign_in_available=bool(
            identity is not None
            and management.provider is not None
            and management.provider.enabled
        ),
        oidc_identity_status=(
            "not_linked"
            if identity is None
            else "linked_available"
            if management.provider is not None and management.provider.enabled
            else "linked_unavailable"
        ),
        credential_management_source=management.password_managed_by(user),
        password_managed_by=management.password_managed_by(user),
        role_managed_by=management.role_managed_by,
        mfa_enabled=management.mfa_enabled,
        mfa_confirmed_at=management.mfa_confirmed_at,
        active_session_count=management.active_session_count,
        security_version=int(user.auth_token_version or 0),
        credentials_rotated=credentials_rotated,
        revoked_api_tokens=revoked_api_tokens,
        revoked_auth_sessions=revoked_auth_sessions,
    )


def _verify_admin_recent_auth(
    db: Session,
    *,
    request: Request,
    admin: User,
    current_password: str | None,
) -> str:
    settings = get_settings()
    session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(settings.auth_cookie_name)
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign out, sign in again, and retry administrator MFA recovery.",
            error_code="opaque_session_required",
        )
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=session_id,
        user_id=admin.id,
        auth_token_version=int(admin.auth_token_version or 0),
    )
    if session is None:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The administrator session is no longer active. Sign in again.",
            error_code="session_inactive",
        )
    if session.auth_method == "local":
        if not admin.password_login_enabled:
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Local password reauthentication is not available for this account.",
                error_code="local_reauthentication_unavailable",
                error_context=recent_authentication_error_context(
                    session,
                    action="administrator_mfa_reset",
                ),
            )
        if not current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter your current password to perform administrator MFA recovery.",
            )
        verify_current_password_or_raise(
            user=admin,
            candidate_password=current_password,
            client_ip=resolve_client_ip(request),
        )
        return "local"

    current_time = datetime.now(timezone.utc)
    if session.auth_method != "oidc":
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reauthenticate with the identity provider before performing administrator MFA recovery.",
            error_code="oidc_reauthentication_required",
            error_context=recent_authentication_error_context(
                session,
                action="administrator_mfa_reset",
            ),
        )
    authenticated_at = session.identity_authenticated_at
    if authenticated_at is None:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The identity provider did not prove a recent authentication for this session. "
                "Complete identity-provider reauthentication and retry the recovery action."
            ),
            error_code="oidc_reauthentication_required",
            error_context=recent_authentication_error_context(
                session,
                action="administrator_mfa_reset",
            ),
        )
    if authenticated_at.tzinfo is None:
        authenticated_at = authenticated_at.replace(tzinfo=timezone.utc)
    age_seconds = (current_time - authenticated_at).total_seconds()
    if age_seconds < -60 or age_seconds > get_settings().auth_recent_auth_seconds:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your identity-provider authentication is too old for this operation. "
                "Complete identity-provider reauthentication and retry the recovery action."
            ),
            error_code="oidc_reauthentication_required",
            error_context=recent_authentication_error_context(
                session,
                action="administrator_mfa_reset",
            ),
        )
    if not auth_session_has_configured_oidc_mfa_assurance(session):
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The identity provider did not assert the configured MFA assurance for this session. "
                "Complete MFA through the identity-provider reauthentication flow."
            ),
            error_code="oidc_reauthentication_required",
            error_context=recent_authentication_error_context(
                session,
                action="administrator_mfa_reset",
            ),
        )
    return "oidc"


def _record_admin_mfa_reset_failure(
    db: Session,
    *,
    admin: User,
    target_user_id: uuid.UUID,
    reason: str,
) -> None:
    db.rollback()
    record_audit(
        db,
        actor_user_id=admin.id,
        action="users.mfa.reset",
        resource_type="user",
        resource_id=str(target_user_id),
        success=False,
        metadata={"reason": reason},
    )
    db.commit()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
