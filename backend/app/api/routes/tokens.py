import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import is_cookie_session_auth, require_token_scopes
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import generate_api_token, verify_password
from app.core.token_scopes import DEFAULT_API_TOKEN_SCOPES, SCOPE_READ_TOKENS, SCOPE_WRITE_TOKENS, missing_delegable_scopes
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.schemas.token import ApiTokenCreateRequest, ApiTokenCreateResponse, ApiTokenResponse
from app.services.audit import record_audit

router = APIRouter(prefix="/tokens", tags=["tokens"])

SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL = (
    "Browser sessions must confirm the current password before creating API tokens"
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
    _enforce_browser_session_step_up(request, payload, user)

    token_value, token_prefix, token_hash = generate_api_token()
    expires_days = payload.expires_in_days or settings.default_api_token_expiry_days
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    scopes = payload.scopes if "scopes" in payload.model_fields_set else list(DEFAULT_API_TOKEN_SCOPES)
    parent_token_scopes = getattr(request.state, "token_scopes", None)
    if parent_token_scopes is not None:
        disallowed_scopes = missing_delegable_scopes(parent_token_scopes, scopes)
        if disallowed_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scoped tokens can only delegate a subset of their own scopes: {', '.join(disallowed_scopes)}",
            )

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
        metadata={"name": token.name, "token_prefix": token.token_prefix},
    )
    db.commit()

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ApiTokenCreateResponse(token=token_value, token_prefix=token_prefix, expires_at=expires_at)


def _enforce_browser_session_step_up(request: Request, payload: ApiTokenCreateRequest, user: User) -> None:
    if not is_cookie_session_auth(request):
        return
    if not payload.current_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SESSION_TOKEN_STEP_UP_REQUIRED_DETAIL)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_TOKENS)),
):
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id))
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    if token.user_id != user.id and user.role != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

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
