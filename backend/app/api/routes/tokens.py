import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_auth_session_id,
    is_cookie_session_auth,
    require_token_scopes,
    resolve_client_ip,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import (
    extract_api_token_prefix,
    generate_api_token,
    hash_api_token,
    verify_password,
)
from app.core.token_scopes import (
    DEFAULT_API_TOKEN_SCOPES,
    SCOPE_READ_TOKENS,
    SCOPE_WRITE_TOKENS,
    missing_delegable_scopes,
    missing_role_token_scopes,
    has_required_scope,
)
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.schemas.token import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListResponse,
    ApiTokenResponse,
)
from app.services.audit import record_audit
from app.services.auth_sessions import lock_exact_auth_session, lock_user_auth_states
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)
from app.services.local_mfa import MFAError, MFAInvalidCodeError, mfa_status
from app.services.mfa_action_verification import (
    MFASensitiveActionRateLimitError,
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)
from app.services.recent_auth import (
    auth_session_has_configured_oidc_mfa_assurance,
    recent_authentication_error_context,
    recent_authentication_state,
)

router = APIRouter(prefix="/tokens", tags=["tokens"])

SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL = (
    "Browser sessions must confirm the current password before creating API tokens"
)
API_TOKEN_CHILD_MAX_LIFETIME = timedelta(hours=1)
API_TOKEN_CHILD_SCOPE_DETAIL = (
    "API tokens cannot mint child tokens with write:tokens scope"
)
API_TOKEN_CHILD_EXPIRED_DETAIL = (
    "Parent API token is too close to expiry to mint a child token"
)
SESSION_TOKEN_SCOPE_DETAIL = (
    "Requested token scopes exceed the permissions allowed for your role"
)


@router.get("", response_model=list[ApiTokenResponse])
def list_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_TOKENS)),
    user_id: uuid.UUID | None = Query(default=None),
):
    target_user_id = user.id
    if user_id is not None:
        if user.role != ROLE_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        target_user_id = user_id

    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == target_user_id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    return list(tokens)


@router.get("/inventory", response_model=ApiTokenListResponse)
def list_token_inventory(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_TOKENS)),
    user_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=25, ge=1, le=100),
):
    target_user_id = user.id
    if user_id is not None:
        if user.role != ROLE_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        target_user_id = user_id

    criteria = (ApiToken.user_id == target_user_id,)
    total = int(
        db.scalar(select(func.count(ApiToken.id)).where(*criteria)) or 0
    )
    tokens = list(
        db.scalars(
            select(ApiToken)
            .where(*criteria)
            .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return ApiTokenListResponse(
        tokens=tokens,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": (
                "Browser creation requires the authentication-method-specific step-up. "
                "OIDC sessions can return `oidc_reauthentication_required` or "
                "`oidc_mfa_assurance_required`."
            )
        }
    },
)
def create_token(
    request: Request,
    payload: ApiTokenCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_TOKENS)),
):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    locked_users = lock_user_auth_states(db, [user.id])
    user = locked_users.get(user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account security changed. Sign in again.",
            error_code="account_security_changed",
        )
    credential_verification = _enforce_browser_session_step_up(
        request, payload, user, db
    )

    token_value, token_prefix, token_hash = generate_api_token()
    scopes = (
        payload.scopes
        if "scopes" in payload.model_fields_set
        else list(DEFAULT_API_TOKEN_SCOPES)
    )
    _enforce_requested_token_scopes_authorized(request, user, scopes)
    parent_token_scopes = getattr(request.state, "token_scopes", None)
    parent_api_token = _resolve_authenticated_parent_api_token(
        request, db, user_id=user.id
    )
    if parent_token_scopes is not None:
        disallowed_scopes = missing_delegable_scopes(parent_token_scopes, scopes)
        if disallowed_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scoped tokens can only delegate a subset of their own scopes: {', '.join(disallowed_scopes)}",
            )

    expires_days = payload.expires_in_days or settings.default_api_token_expiry_days
    expires_at = now + timedelta(days=expires_days)
    if parent_api_token is not None:
        expires_at = _bounded_child_token_expiry(
            parent_api_token=parent_api_token,
            requested_expires_at=expires_at,
            scopes=scopes,
            now=now,
        )

    token = ApiToken(
        user_id=user.id,
        name=payload.name,
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes=scopes,
        parent_token_id=parent_api_token.id if parent_api_token is not None else None,
        expires_at=expires_at,
    )
    db.add(token)
    db.flush()

    record_audit(
        db,
        actor_user_id=user.id,
        action="tokens.create",
        resource_type="api_token",
        resource_id=str(token.id),
        metadata={
            "name": token.name,
            "token_prefix": token.token_prefix,
            "delegated_via_api_token": parent_api_token is not None,
            "parent_token_id": str(parent_api_token.id)
            if parent_api_token is not None
            else None,
            "credential_verification": credential_verification,
        },
    )
    db.commit()

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ApiTokenCreateResponse(
        token=token_value, token_prefix=token_prefix, expires_at=expires_at
    )


def _enforce_browser_session_step_up(
    request: Request,
    payload: ApiTokenCreateRequest,
    user: User,
    db: Session,
) -> str | None:
    if not is_cookie_session_auth(request):
        return None
    action = "api_token_create"
    session_id = get_current_auth_session_id(request)
    session_token = request.cookies.get(get_settings().auth_cookie_name)
    if session_id is None or not session_token:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This legacy browser session cannot create API tokens. Sign out, sign in again, and retry.",
            error_code="opaque_session_required",
            error_context=recent_authentication_error_context(None, action=action),
        )
    session = lock_exact_auth_session(
        db,
        token=session_token,
        expected_session_id=session_id,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
    )
    if session is None:
        raise ApiHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The current browser session is no longer active. Sign in again.",
            error_code="session_inactive",
        )
    if session.auth_method == "oidc":
        recent = recent_authentication_state(session)
        if not recent.valid:
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Reauthenticate with the identity provider before creating an "
                    "API token."
                ),
                error_code="oidc_reauthentication_required",
                error_context=recent_authentication_error_context(
                    session,
                    action=action,
                ),
            )
        if not auth_session_has_configured_oidc_mfa_assurance(session):
            raise ApiHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The identity provider did not assert the configured MFA assurance. "
                    "Complete MFA during identity-provider reauthentication before "
                    "creating an API token."
                ),
                error_code="oidc_mfa_assurance_required",
                error_context=recent_authentication_error_context(
                    session,
                    action=action,
                ),
            )
        return "oidc_recent_authentication"
    if session.auth_method != "local" or not user.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Browser API token creation requires an account with local password authentication",
        )
    if not payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL,
        )
    client_ip = resolve_client_ip(request)
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        detail = (
            "Too many failed current password verification attempts. Try again later."
        )
        headers = (
            {"Retry-After": str(throttle.retry_after_seconds)}
            if throttle.retry_after_seconds
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers=headers,
        )
    if not verify_password(payload.current_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    clear_password_verification_failures(
        user.email,
        client_ip,
        observed_failure_version=throttle.failure_version,
    )
    mfa_enabled, _confirmed_at, _remaining = mfa_status(db, user_id=user.id)
    if not mfa_enabled:
        return "local_password"
    if not payload.code:
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enter a current authenticator or recovery code before creating an API token.",
            error_code="mfa_verification_required",
        )
    try:
        verification = verify_sensitive_mfa_code(
            db,
            user=user,
            code=payload.code,
            client_ip=client_ip,
        )
    except MFASensitiveActionRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds
                else None
            ),
        ) from exc
    except MFAInvalidCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except MFASensitiveActionThrottleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shared MFA verification throttling is temporarily unavailable. No MFA code was checked; try again shortly.",
            headers={"Retry-After": "5"},
        ) from exc
    except MFAError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA verification is temporarily unavailable. Try again later.",
        ) from exc
    return f"local_password_{verification.method}"


def _enforce_requested_token_scopes_authorized(
    request: Request, user: User, scopes: list[str]
) -> None:
    if not is_cookie_session_auth(request):
        return

    disallowed_scopes = missing_role_token_scopes(user.role, scopes)
    if disallowed_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{SESSION_TOKEN_SCOPE_DETAIL}: {', '.join(disallowed_scopes)}",
        )


def _resolve_authenticated_parent_api_token(
    request: Request, db: Session, *, user_id: uuid.UUID
) -> ApiToken | None:
    if not getattr(request.state, "auth_via_api_token", False):
        return None

    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    raw_token = credentials.strip()
    token_prefix = extract_api_token_prefix(raw_token)
    if token_prefix is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    parent_token = db.scalar(
        select(ApiToken)
        .where(
            and_(
                ApiToken.token_prefix == token_prefix,
                ApiToken.token_hash == hash_api_token(raw_token),
                ApiToken.revoked_at.is_(None),
            )
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    if (
        parent_token is None
        or parent_token.user_id != user_id
        or (
            parent_token.expires_at is not None
            and _as_utc(parent_token.expires_at) <= now
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    return parent_token


def _bounded_child_token_expiry(
    *,
    parent_api_token: ApiToken,
    requested_expires_at: datetime,
    scopes: list[str],
    now: datetime,
) -> datetime:
    if has_required_scope(set(scopes), SCOPE_WRITE_TOKENS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=API_TOKEN_CHILD_SCOPE_DETAIL
        )

    expires_at = min(requested_expires_at, now + API_TOKEN_CHILD_MAX_LIFETIME)
    if parent_api_token.expires_at is not None:
        parent_expires_at = parent_api_token.expires_at
        if parent_expires_at.tzinfo is None:
            parent_expires_at = parent_expires_at.replace(tzinfo=timezone.utc)
        expires_at = min(expires_at, parent_expires_at)

    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=API_TOKEN_CHILD_EXPIRED_DETAIL
        )
    return expires_at


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": "The token and every active delegated descendant were revoked.",
            "headers": {
                "X-ThreatLens-Revoked-Token-Count": {
                    "schema": {"type": "integer", "minimum": 0}
                },
                "X-ThreatLens-Revoked-Descendant-Count": {
                    "schema": {"type": "integer", "minimum": 0}
                },
                "X-ThreatLens-Root-Token-Revoked": {"schema": {"type": "boolean"}},
            },
        }
    },
)
def revoke_token(
    token_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_TOKENS)),
):
    token_owner_id = db.scalar(select(ApiToken.user_id).where(ApiToken.id == token_id))
    if token_owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )
    locked_users = lock_user_auth_states(db, [user.id, token_owner_id])
    user = locked_users.get(user.id)
    if user is None or not user.is_active or not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    if user.role != ROLE_ADMIN and token_owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )
    token = db.scalar(
        select(ApiToken)
        .where(ApiToken.id == token_id, ApiToken.user_id == token_owner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )

    impact = _revoke_token_lineage(db, token, now=datetime.now(timezone.utc))

    record_audit(
        db,
        actor_user_id=user.id,
        action="tokens.revoke",
        resource_type="api_token",
        resource_id=str(token.id),
        metadata={
            "token_prefix": token.token_prefix,
            "revoked_token_count": impact.revoked_token_count,
            "revoked_descendant_count": impact.revoked_descendant_count,
            "root_token_revoked": impact.root_token_revoked,
        },
    )
    db.commit()
    response.headers["X-ThreatLens-Revoked-Token-Count"] = str(
        impact.revoked_token_count
    )
    response.headers["X-ThreatLens-Revoked-Descendant-Count"] = str(
        impact.revoked_descendant_count
    )
    response.headers["X-ThreatLens-Root-Token-Revoked"] = str(
        impact.root_token_revoked
    ).lower()


@dataclass(frozen=True)
class TokenRevocationImpact:
    revoked_token_count: int
    revoked_descendant_count: int
    root_token_revoked: bool


def _revoke_token_lineage(
    db: Session,
    token: ApiToken,
    *,
    now: datetime,
) -> TokenRevocationImpact:
    root_token_revoked = token.revoked_at is None
    pending_ids = [token.id]
    all_ids: set[uuid.UUID] = set()
    while pending_ids:
        next_ids = list(
            db.scalars(
                select(ApiToken.id)
                .where(ApiToken.parent_token_id.in_(pending_ids))
                .order_by(ApiToken.id)
                .with_for_update()
            ).all()
        )
        all_ids.update(pending_ids)
        pending_ids = [token_id for token_id in next_ids if token_id not in all_ids]
    result = db.execute(
        update(ApiToken)
        .where(ApiToken.id.in_(all_ids), ApiToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    revoked_token_count = int(result.rowcount or 0)
    return TokenRevocationImpact(
        revoked_token_count=revoked_token_count,
        revoked_descendant_count=max(
            0,
            revoked_token_count - int(root_token_revoked),
        ),
        root_token_revoked=root_token_revoked,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
