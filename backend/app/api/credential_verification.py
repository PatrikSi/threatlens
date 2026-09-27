"""Translate authenticated request state into transport-independent verification."""

from fastapi import Request

from app.api.deps import get_authorization_context, resolve_client_ip
from app.core.config import get_settings
from app.core.credential_types import AUTH_API_TOKEN, AUTH_SESSION_COOKIE
from app.services.credential_verification import CredentialVerificationContext


def credential_verification_context(request: Request) -> CredentialVerificationContext:
    kind = getattr(request.state, "auth_credential_kind", None)
    identifier = {
        AUTH_SESSION_COOKIE: getattr(request.state, "auth_session_id", None),
        AUTH_API_TOKEN: getattr(request.state, "api_token_id", None),
    }.get(kind)
    return CredentialVerificationContext(
        credential_kind=kind,
        credential_id=identifier,
        authorization=get_authorization_context(request),
        client_ip=resolve_client_ip(request),
        session_token=request.cookies.get(get_settings().auth_cookie_name)
        if kind == AUTH_SESSION_COOKIE
        else None,
    )
