from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services import mfa_action_verification
from app.services.auth_sessions import (
    AuthSessionStateError,
    create_auth_session,
    list_user_auth_sessions,
    resolve_auth_session,
    touch_auth_session,
)
from app.services.recent_auth import auth_session_has_configured_oidc_mfa_assurance
from app.services.mfa_action_verification import (
    MFASensitiveActionThrottleUnavailableError,
    verify_sensitive_mfa_code,
)


def test_oidc_mfa_assurance_status_rechecks_the_current_policy(monkeypatch):
    settings = get_settings()
    session = AuthSession(
        auth_method="oidc",
        mfa_method="external",
        identity_acr="urn:company:loa:2",
        identity_amr_json=["pwd", "MFA"],
    )
    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_amr_values", ["mfa"])
    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_acr_values", [])
    assert auth_session_has_configured_oidc_mfa_assurance(session) is True

    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_acr_values", ["loa3"])
    assert auth_session_has_configured_oidc_mfa_assurance(session) is False
    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_acr_values", [])
    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_amr_values", [])
    assert auth_session_has_configured_oidc_mfa_assurance(session) is False

    monkeypatch.setattr(settings, "auth_oidc_admin_mfa_amr_values", ["mfa"])
    session.identity_amr_json = {"mfa": True}
    assert auth_session_has_configured_oidc_mfa_assurance(session) is False


def test_auth_session_enforces_idle_and_absolute_expiry(
    db_session, seed_users, monkeypatch
):
    user = seed_users["admin"]
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.auth_sessions.session_timing_seconds", lambda: (600, 300, 60)
    )
    created = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip=None,
        user_agent=None,
        now=now,
    )
    db_session.commit()

    session = resolve_auth_session(
        db_session, created.token, now=now + timedelta(seconds=120)
    )
    assert session is not None
    assert (
        touch_auth_session(db_session, session, now=now + timedelta(seconds=120))
        is True
    )
    assert session.idle_expires_at == now + timedelta(seconds=420)

    expired = resolve_auth_session(
        db_session, created.token, now=now + timedelta(seconds=601)
    )
    assert expired is None
    db_session.commit()
    db_session.refresh(created.session)
    assert created.session.revoked_reason == "absolute_expired"


def test_auth_session_touch_is_write_bounded(db_session, seed_users, monkeypatch):
    user = seed_users["admin"]
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.auth_sessions.session_timing_seconds", lambda: (600, 300, 60)
    )
    created = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip=None,
        user_agent=None,
        now=now,
    )
    assert (
        touch_auth_session(db_session, created.session, now=now + timedelta(seconds=59))
        is False
    )
    assert created.session.last_seen_at == now


def test_auth_session_rejects_replacement_past_absolute_expiry(
    db_session, seed_users, monkeypatch
):
    user = seed_users["admin"]
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.auth_sessions.session_timing_seconds", lambda: (600, 300, 60)
    )

    with pytest.raises(AuthSessionStateError, match="absolute expiry"):
        create_auth_session(
            db_session,
            user_id=user.id,
            auth_method="local",
            mfa_method=None,
            authenticated_at=now - timedelta(minutes=10),
            absolute_expires_at=now,
            client_ip=None,
            user_agent=None,
            now=now,
        )


def test_sensitive_mfa_verification_does_not_touch_credential_without_shared_throttle(
    db_session,
    seed_users,
    monkeypatch,
):
    user: User = seed_users["admin"]
    monkeypatch.setattr(
        mfa_action_verification,
        "check_mfa_action_throttle",
        lambda _email, _ip: SimpleNamespace(
            backend_available=False,
            blocked=False,
            retry_after_seconds=None,
        ),
    )

    def _unexpected_verification(*_args, **_kwargs):
        raise AssertionError("MFA credential verification must not run")

    monkeypatch.setattr(
        mfa_action_verification,
        "verify_active_mfa_code",
        _unexpected_verification,
    )

    with pytest.raises(MFASensitiveActionThrottleUnavailableError):
        verify_sensitive_mfa_code(
            db_session,
            user=user,
            code="123456",
            client_ip="192.0.2.10",
        )


def test_new_session_revokes_oldest_session_above_per_user_limit(
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["admin"]
    monkeypatch.setattr(get_settings(), "auth_max_active_sessions_per_user", 2)
    now = datetime.now(timezone.utc)
    first = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip="192.0.2.1",
        user_agent="First",
    )
    first.session.created_at = now - timedelta(minutes=2)
    db_session.flush()
    second = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip="192.0.2.2",
        user_agent="Second",
    )
    second.session.created_at = now - timedelta(minutes=1)
    db_session.flush()
    third = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip="192.0.2.3",
        user_agent="Third",
    )
    db_session.flush()

    db_session.refresh(first.session)
    db_session.refresh(second.session)
    db_session.refresh(third.session)
    assert first.session.revoked_reason == "active_session_limit"
    assert second.session.revoked_at is None
    assert third.session.revoked_at is None


def test_session_inventory_keeps_active_sessions_separate_from_capped_history(
    db_session,
    seed_users,
):
    user = seed_users["admin"]
    now = datetime.now(timezone.utc)
    active = create_auth_session(
        db_session,
        user_id=user.id,
        auth_method="local",
        mfa_method=None,
        client_ip="192.0.2.10",
        user_agent="Active browser",
        now=now,
    )
    db_session.add_all(
        [
            AuthSession(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=f"{index:064x}",
                auth_token_version=user.auth_token_version,
                auth_method="local",
                mfa_method=None,
                authenticated_at=now,
                last_seen_at=now,
                idle_expires_at=now - timedelta(minutes=1),
                absolute_expires_at=now + timedelta(days=1),
                revoked_at=now,
                revoked_reason="test_history",
                created_at=now + timedelta(seconds=index + 1),
                updated_at=now,
            )
            for index in range(205)
        ]
    )
    db_session.flush()

    inventory = list_user_auth_sessions(
        db_session,
        user_id=user.id,
        current_session_id=active.session.id,
        now=now,
    )

    assert inventory.active_count == 1
    assert inventory.active_truncated is False
    assert inventory.history_truncated is True
    assert len(inventory.sessions) == 201
    assert inventory.sessions[0].id == active.session.id
    assert inventory.sessions[0].revoked_at is None
