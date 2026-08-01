import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import is_cookie_session_auth, require_token_scopes, resolve_client_ip
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import extract_api_token_prefix, generate_api_token, hash_api_token, verify_password
from app.core.token_scopes import (
    DEFAULT_API_TOKEN_SCOPES,
    SCOPE_READ_TOKENS,
    SCOPE_WRITE_TOKENS,
    missing_delegable_scopes,
    missing_role_token_scopes,
)
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.schemas.token import ApiTokenCreateRequest, ApiTokenCreateResponse, ApiTokenResponse
from app.services.audit import record_audit
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    clear_password_verification_failures,
    record_password_verification_failure,
)

router = APIRouter(prefix="/tokens", tags=["tokens"])

SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL = (
    "Browser sessions must confirm the current password before creating API tokens"
)
API_TOKEN_CHILD_MAX_LIFETIME = timedelta(hours=1)
API_TOKEN_CHILD_SCOPE_DETAIL = "API tokens cannot mint child tokens with write:tokens scope"
API_TOKEN_CHILD_EXPIRED_DETAIL = "Parent API token is too close to expiry to mint a child token"
SESSION_TOKEN_SCOPE_DETAIL = "Requested token scopes exceed the permissions allowed for your role"


@router.get("", response_model=list[ApiTokenResponse])
def list_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_TOKENS)),
    user_id: uuid.UUID | None = Query(default=None),
):
    target_user_id = user.id
    if user_id is not None:
        if user.role != ROLE_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        target_user_id = user_id

    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == target_user_id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    return list(tokens)


@router.post("", response_model=ApiTokenCreateResponse, status_code=status.HTTP_201_CREATED)
def create_token(
    request: Request,
    payload: ApiTokenCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_TOKENS)),
):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    _enforce_browser_session_step_up(request, payload, user)

    token_value, token_prefix, token_hash = generate_api_token()
    scopes = payload.scopes if "scopes" in payload.model_fields_set else list(DEFAULT_API_TOKEN_SCOPES)
    _enforce_requested_token_scopes_authorized(request, user, scopes)
    parent_token_scopes = getattr(request.state, "token_scopes", None)
    parent_api_token = _resolve_authenticated_parent_api_token(request, db)
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
        expires_at = _bounded_child_token_expiry(parent_api_token=parent_api_token, requested_expires_at=expires_at, scopes=scopes, now=now)

    token = ApiToken(
        user_id=user.id,
        name=payload.name,
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes=scopes,
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
            "parent_token_id": str(parent_api_token.id) if parent_api_token is not None else None,
        },
    )
    db.commit()

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ApiTokenCreateResponse(token=token_value, token_prefix=token_prefix, expires_at=expires_at)


def _enforce_browser_session_step_up(request: Request, payload: ApiTokenCreateRequest, user: User) -> None:
    if not is_cookie_session_auth(request):
        return
    if not user.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Browser API token creation requires an account with local password authentication",
        )
    if not payload.current_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL)
    client_ip = resolve_client_ip(request)
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        detail = "Too many failed current password verification attempts. Try again later."
        headers = {"Retry-After": str(throttle.retry_after_seconds)} if throttle.retry_after_seconds else None
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)
    if not verify_password(payload.current_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    clear_password_verification_failures(user.email, client_ip)


def _enforce_requested_token_scopes_authorized(request: Request, user: User, scopes: list[str]) -> None:
    if not is_cookie_session_auth(request):
        return

    disallowed_scopes = missing_role_token_scopes(user.role, scopes)
    if disallowed_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{SESSION_TOKEN_SCOPE_DETAIL}: {', '.join(disallowed_scopes)}",
        )


def _resolve_authenticated_parent_api_token(request: Request, db: Session) -> ApiToken | None:
    if not getattr(request.state, "auth_via_api_token", False):
        return None

    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    raw_token = credentials.strip()
    token_prefix = extract_api_token_prefix(raw_token)
    if token_prefix is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    parent_token = db.scalar(
        select(ApiToken).where(
            and_(
                ApiToken.token_prefix == token_prefix,
                ApiToken.token_hash == hash_api_token(raw_token),
                ApiToken.revoked_at.is_(None),
            )
        )
    )
    if parent_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return parent_token


def _bounded_child_token_expiry(
    *,
    parent_api_token: ApiToken,
    requested_expires_at: datetime,
    scopes: list[str],
    now: datetime,
) -> datetime:
    if SCOPE_WRITE_TOKENS in scopes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=API_TOKEN_CHILD_SCOPE_DETAIL)

    expires_at = min(requested_expires_at, now + API_TOKEN_CHILD_MAX_LIFETIME)
    if parent_api_token.expires_at is not None:
        parent_expires_at = parent_api_token.expires_at
        if parent_expires_at.tzinfo is None:
            parent_expires_at = parent_expires_at.replace(tzinfo=timezone.utc)
        expires_at = min(expires_at, parent_expires_at)

    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=API_TOKEN_CHILD_EXPIRED_DETAIL)
    return expires_at


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_TOKENS)),
):
    token_query = select(ApiToken).where(ApiToken.id == token_id)
    if user.role != ROLE_ADMIN:
        token_query = token_query.where(ApiToken.user_id == user.id)
    token = db.scalar(token_query)
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.add(token)

    record_audit(
        db,
        actor_user_id=user.id,
        action="tokens.revoke",
        resource_type="api_token",
        resource_id=str(token.id),
        metadata={"token_prefix": token.token_prefix},
    )
    db.commit()
