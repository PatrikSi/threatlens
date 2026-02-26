import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN
from app.core.security import generate_api_token
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.schemas.token import ApiTokenCreateRequest, ApiTokenCreateResponse, ApiTokenResponse
from app.services.audit import record_audit

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("", response_model=list[ApiTokenResponse])
def list_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
def create_token(payload: ApiTokenCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()

    token_value, token_prefix, token_hash = generate_api_token()
    expires_days = payload.expires_in_days or settings.default_api_token_expiry_days
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    token = ApiToken(
        user_id=user.id,
        name=payload.name,
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes=payload.scopes,
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

    return ApiTokenCreateResponse(token=token_value, token_prefix=token_prefix, expires_at=expires_at)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
