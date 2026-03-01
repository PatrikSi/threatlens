import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Response
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
API_TOKEN_MARKER = "tlp"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expires_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def access_token_ttl_seconds() -> int:
    settings = get_settings()
    return max(60, int(settings.jwt_expires_minutes) * 60)


def decode_access_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def generate_api_token() -> tuple[str, str, str]:
    public_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    token = f"{API_TOKEN_MARKER}_{public_id}_{secret}"
    return token, f"{API_TOKEN_MARKER}_{public_id}", hash_api_token(token)


def extract_api_token_prefix(token: str) -> str | None:
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None

    marker, public_id, _secret = parts
    if marker != API_TOKEN_MARKER or not public_id:
        return None

    return f"{marker}_{public_id}"


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, access_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = access_token_ttl_seconds()
    cookie_common = {
        "max_age": max_age,
        "domain": settings.auth_cookie_domain,
        "path": settings.auth_cookie_path,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
    }

    response.set_cookie(
        key=settings.auth_cookie_name,
        value=access_token,
        httponly=True,
        **cookie_common,
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        **cookie_common,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    cookie_common = {
        "domain": settings.auth_cookie_domain,
        "path": settings.auth_cookie_path,
    }
    response.delete_cookie(settings.auth_cookie_name, **cookie_common)
    response.delete_cookie(settings.auth_csrf_cookie_name, **cookie_common)
