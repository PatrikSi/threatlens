import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.security import decode_access_token, extract_api_token_prefix, hash_api_token
from app.core.config import get_settings
from app.core.token_scopes import has_required_scope, normalize_token_scopes
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)



def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
) -> User:
    request.state.token_scopes = None

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = _resolve_jwt_user(db, token)
    if user is not None:
        return user

    token_result = _resolve_api_token_user(db, token)
    if token_result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user, scopes = token_result
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    request.state.token_scopes = scopes
    return user



def require_roles(*roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _checker


get_operator_user = require_roles(ROLE_ADMIN, ROLE_ANALYST)
get_admin_user = require_roles(ROLE_ADMIN)


def require_token_scopes(*required_scopes: str):
    def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            return user

        granted = set(token_scopes)
        settings = get_settings()

        if not granted and settings.allow_legacy_unscoped_tokens:
            return user

        for required_scope in required_scopes:
            if not has_required_scope(granted, required_scope):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient token scope")

        return user

    return _checker



def _resolve_jwt_user(db: Session, token: str) -> User | None:
    subject = decode_access_token(token)
    if subject is None:
        return None

    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        return None

    return db.scalar(select(User).where(User.id == user_id))



def _resolve_api_token_user(db: Session, token: str) -> tuple[User, list[str]] | None:
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

    scopes = normalize_token_scopes(api_token.scopes)
    if _should_update_last_used(api_token.last_used_at, now):
        api_token.last_used_at = now
        db.add(api_token)
        try:
            db.commit()
        except Exception:
            db.rollback()
    return user, scopes


def _should_update_last_used(last_used_at: datetime | None, now: datetime) -> bool:
    if last_used_at is None:
        return True
    if last_used_at.tzinfo is None:
        last_used_at = last_used_at.replace(tzinfo=timezone.utc)

    settings = get_settings()
    elapsed = (now - last_used_at).total_seconds()
    return elapsed >= settings.api_token_last_used_update_interval_seconds
