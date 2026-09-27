import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.api.credential_verification import credential_verification_context
from app.services.credential_verification import (
    enforce_browser_token_step_up,
    enforce_delegable_token_scopes,
)
from app.api.deps import (
    require_permissions,
)
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import (
    extract_api_token_prefix,
    generate_api_token,
    hash_api_token,
)
from app.core.token_scopes import (
    DEFAULT_API_TOKEN_SCOPES,
    SCOPE_READ_TOKENS,
    SCOPE_WRITE_TOKENS,
    missing_delegable_scopes,
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
from app.services.auth_sessions import lock_user_auth_states

router = APIRouter(prefix="/tokens", tags=["tokens"])

API_TOKEN_CHILD_MAX_LIFETIME = timedelta(hours=1)
API_TOKEN_CHILD_SCOPE_DETAIL = (
    "API tokens cannot mint child tokens with write:tokens scope"
)
API_TOKEN_CHILD_EXPIRED_DETAIL = (
    "Parent API token is too close to expiry to mint a child token"
)


@router.get("", response_model=list[ApiTokenResponse])
def list_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_TOKENS)),
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
    user: User = Depends(require_permissions(SCOPE_READ_TOKENS)),
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
    total = int(db.scalar(select(func.count(ApiToken.id)).where(*criteria)) or 0)
    unscoped_total = int(
        db.scalar(
            select(func.count(ApiToken.id)).where(
                *criteria,
                ApiToken.scopes == [],
            )
        )
        or 0
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
        unscoped_total=unscoped_total,
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
    user: User = Depends(require_permissions(SCOPE_WRITE_TOKENS)),
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
    verification_context = credential_verification_context(request)
    credential_verification = enforce_browser_token_step_up(
        db,
        context=verification_context,
        user=user,
        current_password=payload.current_password,
        mfa_code=payload.code,
    )

    token_value, token_prefix, token_hash = generate_api_token()
    scopes = (
        payload.scopes
        if "scopes" in payload.model_fields_set
        else list(DEFAULT_API_TOKEN_SCOPES)
    )
    enforce_delegable_token_scopes(verification_context, user, scopes)
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
    user: User = Depends(require_permissions(SCOPE_WRITE_TOKENS)),
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
