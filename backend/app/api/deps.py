import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.security import decode_access_token, extract_api_token_prefix, hash_api_token
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)



def get_current_user(db: Session = Depends(get_db), token: str | None = Depends(oauth2_scheme)) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = _resolve_jwt_user(db, token)
    if user is None:
        user = _resolve_api_token_user(db, token)

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    return user



def require_roles(*roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _checker


get_operator_user = require_roles(ROLE_ADMIN, ROLE_ANALYST)
get_admin_user = require_roles(ROLE_ADMIN)



def _resolve_jwt_user(db: Session, token: str) -> User | None:
    subject = decode_access_token(token)
    if subject is None:
        return None

    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        return None

    return db.scalar(select(User).where(User.id == user_id))



def _resolve_api_token_user(db: Session, token: str) -> User | None:
    prefix = extract_api_token_prefix(token)
    if prefix is None:
        return None

    token_hash = hash_api_token(token)
    now = datetime.now(timezone.utc)

    api_token = db.scalar(
        select(ApiToken).where(
            and_(
                ApiToken.token_prefix == prefix,
                ApiToken.token_hash == token_hash,
                ApiToken.revoked_at.is_(None),
            )
        )
    )
    if api_token is None:
        return None

    if api_token.expires_at is not None:
        expires_at = api_token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            return None

    user = db.scalar(select(User).where(User.id == api_token.user_id))
    if user is None:
        return None

    api_token.last_used_at = now
    db.add(api_token)
    db.commit()
    return user
