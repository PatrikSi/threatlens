from __future__ import annotations

import hmac
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Request, Response
from jwt import InvalidTokenError

from app.core.config import get_settings

OIDC_TRANSACTION_AUDIENCE = "threatlens:oidc-transaction"
OIDCTransactionMode = Literal["login", "link", "reauth"]


@dataclass(frozen=True)
class OIDCTransaction:
    provider_id: str
    provider_config_revision: int | None
    state: str
    nonce: str
    code_verifier: str
    mode: OIDCTransactionMode
    user_id: str | None
    session_binding: str | None
    auth_token_version: int | None
    earliest_auth_time: int | None


def new_oidc_transaction(
    *,
    provider_id: str,
    provider_config_revision: int,
    mode: OIDCTransactionMode,
    user_id: str | None = None,
    session_binding: str | None = None,
    auth_token_version: int | None = None,
    earliest_auth_time: int | None = None,
) -> OIDCTransaction:
    return OIDCTransaction(
        provider_id=provider_id,
        provider_config_revision=provider_config_revision,
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        mode=mode,
        user_id=user_id,
        session_binding=session_binding,
        auth_token_version=auth_token_version,
        earliest_auth_time=earliest_auth_time,
    )


def encode_oidc_transaction(transaction: OIDCTransaction) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "aud": OIDC_TRANSACTION_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=settings.oidc_transaction_ttl_seconds),
        "provider_id": transaction.provider_id,
        "provider_config_revision": transaction.provider_config_revision,
        "state": transaction.state,
        "nonce": transaction.nonce,
        "code_verifier": transaction.code_verifier,
        "mode": transaction.mode,
        "user_id": transaction.user_id,
        "session_binding": transaction.session_binding,
        "auth_token_version": transaction.auth_token_version,
        "earliest_auth_time": transaction.earliest_auth_time,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_oidc_transaction(
    request: Request, returned_state: str | None
) -> OIDCTransaction | None:
    settings = get_settings()
    raw_token = request.cookies.get(settings.oidc_transaction_cookie_name)
    if not raw_token or not returned_state:
        return None
    try:
        payload = jwt.decode(
            raw_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=OIDC_TRANSACTION_AUDIENCE,
            options={
                "require": [
                    "aud",
                    "exp",
                    "provider_id",
                    "state",
                    "nonce",
                    "code_verifier",
                    "mode",
                ]
            },
        )
    except InvalidTokenError:
        return None
    state = payload.get("state")
    if not isinstance(state, str) or not hmac.compare_digest(state, returned_state):
        return None
    mode = payload.get("mode")
    if mode not in {"login", "link", "reauth"}:
        return None
    values = (
        payload.get("provider_id"),
        payload.get("nonce"),
        payload.get("code_verifier"),
    )
    if not all(isinstance(value, str) and value for value in values):
        return None
    user_id = payload.get("user_id")
    if user_id is not None and not isinstance(user_id, str):
        return None
    session_binding = payload.get("session_binding")
    if session_binding is not None and (
        not isinstance(session_binding, str) or len(session_binding) != 64
    ):
        return None
    auth_token_version = payload.get("auth_token_version")
    if auth_token_version is not None and (
        isinstance(auth_token_version, bool) or not isinstance(auth_token_version, int)
    ):
        return None
    provider_config_revision = payload.get("provider_config_revision")
    if provider_config_revision is not None and (
        isinstance(provider_config_revision, bool)
        or not isinstance(provider_config_revision, int)
        or provider_config_revision < 1
    ):
        return None
    earliest_auth_time = payload.get("earliest_auth_time")
    if earliest_auth_time is not None and (
        isinstance(earliest_auth_time, bool) or not isinstance(earliest_auth_time, int)
    ):
        return None
    return OIDCTransaction(
        provider_id=values[0],
        provider_config_revision=provider_config_revision,
        state=state,
        nonce=values[1],
        code_verifier=values[2],
        mode=mode,
        user_id=user_id,
        session_binding=session_binding,
        auth_token_version=auth_token_version,
        earliest_auth_time=earliest_auth_time,
    )


def oidc_session_binding(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def set_oidc_transaction_cookie(
    response: Response, transaction: OIDCTransaction
) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.oidc_transaction_cookie_name,
        value=encode_oidc_transaction(transaction),
        max_age=settings.oidc_transaction_ttl_seconds,
        domain=settings.auth_cookie_domain,
        path=settings.auth_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_oidc_transaction_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.oidc_transaction_cookie_name,
        domain=settings.auth_cookie_domain,
        path=settings.auth_cookie_path,
    )
