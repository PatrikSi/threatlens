from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.core.config import get_settings
from app.models.auth_session import AuthSession


RecentAuthMethod = Literal["local", "oidc"]

LOCAL_REAUTHENTICATION_ENDPOINT = "/auth/security/reauthenticate"
OIDC_REAUTHENTICATION_ENDPOINT = "/auth/oidc/reauth"


@dataclass(frozen=True)
class RecentAuthenticationState:
    auth_method: RecentAuthMethod
    authenticated_at: datetime | None
    valid_until: datetime | None
    valid: bool
    reauthentication_endpoint: str


def recent_authentication_state(
    session: AuthSession,
    *,
    now: datetime | None = None,
) -> RecentAuthenticationState:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    authenticated_at = (
        session.identity_authenticated_at
        if session.auth_method == "oidc"
        else session.authenticated_at
    )
    normalized_authenticated_at = (
        _as_utc(authenticated_at) if authenticated_at is not None else None
    )
    valid_until = (
        normalized_authenticated_at
        + timedelta(seconds=get_settings().auth_recent_auth_seconds)
        if normalized_authenticated_at is not None
        else None
    )
    age_seconds = (
        (current_time - normalized_authenticated_at).total_seconds()
        if normalized_authenticated_at is not None
        else None
    )
    return RecentAuthenticationState(
        auth_method="oidc" if session.auth_method == "oidc" else "local",
        authenticated_at=normalized_authenticated_at,
        valid_until=valid_until,
        valid=bool(
            age_seconds is not None
            and age_seconds >= -60
            and age_seconds <= get_settings().auth_recent_auth_seconds
        ),
        reauthentication_endpoint=(
            OIDC_REAUTHENTICATION_ENDPOINT
            if session.auth_method == "oidc"
            else LOCAL_REAUTHENTICATION_ENDPOINT
        ),
    )


def recent_authentication_error_context(
    session: AuthSession | None,
    *,
    action: str,
) -> dict[str, object]:
    auth_method: RecentAuthMethod | None = None
    endpoint: str | None = None
    if session is not None:
        state = recent_authentication_state(session)
        auth_method = state.auth_method
        endpoint = state.reauthentication_endpoint
    return {
        "action": action,
        "reauthentication_method": auth_method,
        "reauthentication_endpoint": endpoint,
    }


def configured_oidc_mfa_assurance_matches(
    *,
    identity_acr: str | None,
    identity_amr: list[str] | None,
) -> bool:
    settings = get_settings()
    required_amr = set(settings.auth_oidc_admin_mfa_amr_values)
    asserted_amr = {
        value.strip().lower()
        for value in (identity_amr or [])
        if isinstance(value, str) and value.strip()
    }
    if not required_amr or not required_amr.intersection(asserted_amr):
        return False
    return (
        not settings.auth_oidc_admin_mfa_acr_values
        or identity_acr in settings.auth_oidc_admin_mfa_acr_values
    )


def auth_session_has_configured_oidc_mfa_assurance(session: AuthSession) -> bool:
    identity_amr = (
        session.identity_amr_json
        if isinstance(session.identity_amr_json, list)
        else []
    )
    return bool(
        session.auth_method == "oidc"
        and session.mfa_method == "external"
        and configured_oidc_mfa_assurance_matches(
            identity_acr=session.identity_acr,
            identity_amr=identity_amr,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "LOCAL_REAUTHENTICATION_ENDPOINT",
    "OIDC_REAUTHENTICATION_ENDPOINT",
    "RecentAuthenticationState",
    "auth_session_has_configured_oidc_mfa_assurance",
    "configured_oidc_mfa_assurance_matches",
    "recent_authentication_error_context",
    "recent_authentication_state",
]
