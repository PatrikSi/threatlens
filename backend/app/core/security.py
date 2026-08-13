import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Response
import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import get_settings

_argon2_hasher = Argon2Hasher()
_bcrypt_hasher = BcryptHasher(rounds=12, prefix="2b")
_password_hash = PasswordHash((_argon2_hasher, _bcrypt_hasher))
API_TOKEN_MARKER = "tlp"
LEGACY_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2x$", "$2y$")
LEGACY_BCRYPT_SHA256_PREFIX = "$bcrypt-sha256$"
BCRYPT_MAX_PASSWORD_BYTES = 72
_BCRYPT_SHA256_V2_RE = re.compile(
    r"^\$bcrypt-sha256\$v=(?P<version>\d+),t=(?P<type>2b),r=(?P<rounds>\d{1,2})"
    r"\$(?P<salt>[^$]{22})\$(?P<checksum>[^$]{31})$"
)
_BCRYPT_SHA256_V1_RE = re.compile(
    r"^\$bcrypt-sha256\$(?P<type>2[ab]),(?P<rounds>\d{1,2})"
    r"\$(?P<salt>[^$]{22})\$(?P<checksum>[^$]{31})$"
)


class _PasswordContextCompatibility:
    """Small compatibility surface for callers that used Passlib's context."""

    @staticmethod
    def identify(hashed_password: str) -> str | None:
        if hashed_password.startswith("$argon2"):
            return "argon2"
        if hashed_password.startswith(LEGACY_BCRYPT_SHA256_PREFIX):
            return "bcrypt_sha256"
        if hashed_password.startswith(LEGACY_BCRYPT_PREFIXES):
            return "bcrypt"
        return None

    @staticmethod
    def hash(password: str, *, scheme: str | None = None) -> str:
        if scheme is None or scheme == "argon2":
            return _argon2_hasher.hash(password)
        if scheme == "bcrypt":
            return _bcrypt_hasher.hash(password)
        raise ValueError(f"Unsupported password hash scheme: {scheme}")

    @staticmethod
    def verify(password: str, hashed_password: str) -> bool:
        return verify_password(password, hashed_password)


pwd_context = _PasswordContextCompatibility()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    verified, _replacement_hash = verify_password_and_update(
        plain_password,
        hashed_password,
    )
    return verified


def verify_password_and_update(
    plain_password: str,
    hashed_password: str,
) -> tuple[bool, str | None]:
    if hashed_password.startswith(LEGACY_BCRYPT_SHA256_PREFIX):
        verified = _verify_legacy_bcrypt_sha256(plain_password, hashed_password)
        return verified, get_password_hash(plain_password) if verified else None
    if _is_legacy_bcrypt_hash(hashed_password) and len(plain_password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        return False, None
    try:
        return _password_hash.verify_and_update(plain_password, hashed_password)
    except (TypeError, UnknownHashError, ValueError):
        return False, None


def get_password_hash(password: str) -> str:
    return _argon2_hasher.hash(password)


def _is_legacy_bcrypt_hash(hashed_password: str) -> bool:
    return hashed_password.startswith(LEGACY_BCRYPT_PREFIXES)


def _verify_legacy_bcrypt_sha256(
    plain_password: str,
    hashed_password: str,
) -> bool:
    match = _BCRYPT_SHA256_V2_RE.fullmatch(hashed_password)
    version = 2
    if match is None:
        match = _BCRYPT_SHA256_V1_RE.fullmatch(hashed_password)
        version = 1
    if match is None or (version == 2 and match.group("version") != "2"):
        return False

    password_bytes = plain_password.encode("utf-8")
    salt = match.group("salt")
    if version == 2:
        digest = hmac.new(salt.encode("ascii"), password_bytes, hashlib.sha256).digest()
    else:
        digest = hashlib.sha256(password_bytes).digest()
    bcrypt_password = base64.b64encode(digest)
    bcrypt_hash = (
        f"${match.group('type')}${int(match.group('rounds')):02d}$"
        f"{salt}{match.group('checksum')}"
    )
    try:
        return _bcrypt_hasher.verify(bcrypt_password, bcrypt_hash)
    except (TypeError, ValueError):
        return False


def create_access_token(subject: str, *, token_version: int = 0) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expires_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "ver": int(token_version)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def access_token_ttl_seconds() -> int:
    settings = get_settings()
    return max(60, int(settings.jwt_expires_minutes) * 60)


def decode_access_token_claims(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except InvalidTokenError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def decode_access_token(token: str) -> str | None:
    payload = decode_access_token_claims(token)
    if payload is None:
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
