from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

from fastapi import Request
from fastapi.testclient import TestClient
import pyotp
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, object_session

from app.api.deps import AUTH_SESSION_COOKIE
from app.api.routes import oidc as oidc_routes
from app.api.routes import oidc_provider as oidc_provider_routes
from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.security import generate_api_token, get_password_hash
from app.db.session import get_db
from app.main import app
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.iam import IAMRole, IAMRolePermission, IAMUserRoleAssignment
from app.models.investigation import Investigation, InvestigationMember
from app.models.mfa import UserTOTPCredential
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.schemas.oidc import OIDCProviderUpdateRequest
from app.services.oidc_client import OIDCClaims, OIDCMetadata, OIDCProtocolError
from app.services.auth_sessions import (
    create_auth_session,
    extract_auth_session_id,
    hash_session_token,
)
from app.services.secret_storage import decrypt_text, is_encrypted_text
from app.services.user_access import load_user_for_access_update


def _provider_payload(**overrides):
    payload = {
        "name": "Acme SSO",
        "enabled": True,
        "issuer_url": "https://idp.example.com",
        "client_id": "threatlens",
        "client_secret": "provider-secret",
        "client_auth_method": "client_secret_basic",
        "public_base_url": "https://threatlens.example.com",
        "scopes": ["openid", "profile", "email", "groups"],
        "role_claim": "groups",
        "role_mappings": [{"claim_value": "soc", "role": "analyst"}],
        "default_role": "viewer",
        "jit_provisioning_enabled": True,
        "auto_approve_users": True,
        "require_verified_email": True,
        "sync_roles_on_login": True,
    }
    payload.update(overrides)
    return payload


def _api_token_headers(
    db_session: Session,
    user: User,
    *,
    scopes: list[str],
) -> dict[str, str]:
    token, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"oidc-provider-test-{uuid.uuid4()}",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def _configured_provider(db_session, **overrides) -> OIDCProvider:
    values = {
        "system_key": "primary",
        "name": "Acme SSO",
        "enabled": True,
        "issuer_url": "https://idp.example.com",
        "client_id": "threatlens",
        "client_secret_encrypted": None,
        "client_auth_method": "none",
        "public_base_url": "http://testserver",
        "scopes": ["openid", "profile", "email", "groups"],
        "role_claim": "groups",
        "role_mappings_json": [{"claim_value": "soc", "role": "analyst"}],
        "default_role": "viewer",
        "jit_provisioning_enabled": True,
        "auto_approve_users": True,
        "require_verified_email": True,
        "sync_roles_on_login": True,
    }
    values.update(overrides)
    provider = OIDCProvider(**values)
    db_session.add(provider)
    db_session.commit()
    return provider


def _mock_oidc_flow(monkeypatch, claims: dict):
    claims = {"auth_time": int(time.time()), **claims}
    metadata = OIDCMetadata(
        issuer="https://idp.example.com",
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint="https://idp.example.com/userinfo",
        token_endpoint_auth_methods_supported=("none",),
        id_token_signing_alg_values_supported=("RS256",),
    )

    def _load_metadata(provider):
        assert object_session(provider) is None
        return metadata

    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", _load_metadata)
    monkeypatch.setattr(
        "app.api.routes.oidc.build_oidc_authorization_url",
        lambda _provider, _metadata, *, state, **_kwargs: (
            f"https://idp.example.com/authorize?state={state}"
        ),
    )
    monkeypatch.setattr(
        "app.api.routes.oidc.exchange_oidc_code",
        lambda _provider, _metadata, *, code, code_verifier: {
            "id_token": "id-token",
            "access_token": "access-token",
        },
    )
    monkeypatch.setattr(
        "app.api.routes.oidc.validate_oidc_token_claims",
        lambda _provider, _metadata, _token, *, nonce: OIDCClaims(
            issuer="https://idp.example.com",
            subject=str(claims.get("sub", "subject-1")),
            claims=claims,
        ),
    )


def _linked_oidc_session(db_session, user: User) -> tuple[OIDCProvider, str]:
    provider = _configured_provider(db_session)
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=user.id,
            issuer=provider.issuer_url,
            subject="reauth-subject",
            email_at_link=user.email,
        )
    )
    created = create_auth_session(
        db_session,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
        auth_method="oidc",
        mfa_method=None,
        identity_authenticated_at=None,
        client_ip="testclient",
        user_agent="pytest reauth",
    )
    db_session.commit()
    return provider, created.token


def _start_and_complete(client: TestClient, *, start_path: str = "/auth/oidc/login"):
    if start_path == "/auth/oidc/link":
        csrf_token = client.cookies.get("threatlens_csrf")
        assert csrf_token
        start = client.post(
            start_path,
            headers={"X-CSRF-Token": csrf_token},
            json={"current_password": "AnalystPass123!"},
        )
        assert start.status_code == 200
        authorization_url = start.json()["authorization_url"]
    elif start_path == "/auth/oidc/reauth":
        csrf_token = client.cookies.get("threatlens_csrf")
        assert csrf_token
        start = client.post(
            start_path,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert start.status_code == 200, start.text
        authorization_url = start.json()["authorization_url"]
    else:
        start = client.get(start_path, follow_redirects=False)
        assert start.status_code == 302
        authorization_url = start.headers["location"]
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    return client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )


def _local_admin_browser_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login.status_code == 200, login.text
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def test_admin_can_configure_oidc_without_secret_disclosure(
    client, auth_headers, db_session
):
    browser_headers = _local_admin_browser_headers(client)
    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(client_secret="  provider-secret  "),
        headers=browser_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["has_client_secret"] is True
    assert body["require_verified_email"] is True
    assert "client_secret" not in body
    assert (
        body["callback_url"]
        == "https://threatlens.example.com/api/v1/auth/oidc/callback"
    )
    assert body["callback_path"] == "/api/v1/auth/oidc/callback"
    provider = db_session.scalar(select(OIDCProvider))
    assert provider is not None
    assert is_encrypted_text(provider.client_secret_encrypted)
    assert "provider-secret" not in provider.client_secret_encrypted
    assert decrypt_text(provider.client_secret_encrypted) == "  provider-secret  "
    stored_secret = provider.client_secret_encrypted

    retain_payload = _provider_payload(name="Renamed SSO")
    retain_payload.pop("client_secret")
    retained = client.put(
        "/auth/oidc/provider", json=retain_payload, headers=browser_headers
    )
    assert retained.status_code == 200
    assert retained.json()["has_client_secret"] is True
    db_session.refresh(provider)
    assert provider.client_secret_encrypted == stored_secret

    clear_payload = _provider_payload(
        name="Renamed SSO",
        enabled=False,
        client_auth_method="none",
        clear_client_secret=True,
    )
    clear_payload.pop("client_secret")
    cleared = client.put(
        "/auth/oidc/provider", json=clear_payload, headers=browser_headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_client_secret"] is False
    db_session.refresh(provider)
    assert provider.client_secret_encrypted is None

    public = client.get("/auth/oidc/settings")
    assert public.status_code == 200
    public_body = public.json()
    assert public_body["enabled"] is False
    assert public_body["provider_name"] is None
    assert public_body["flow_contract"]["reauthentication_start_path"] == (
        "/auth/oidc/reauth"
    )
    assert public_body["flow_contract"]["reauthentication_redirect_path"] == (
        "/settings/account"
    )
    assert public_body["flow_contract"]["callback_rate_limited_code"] == (
        "callback_rate_limited"
    )
    assert public_body["flow_contract"]["provider_configuration_changed_code"] == (
        "provider_configuration_changed"
    )
    assert set(public_body["flow_contract"]["reauthentication_error_codes"]) >= {
        "callback_rate_limited",
        "provider_configuration_changed",
        "reauth_session_expired",
    }
    assert set(public_body["flow_contract"]["reauthentication_start_error_codes"]) == {
        "browser_session_required",
        "opaque_session_required",
        "oidc_session_required",
        "session_inactive",
        "oidc_provider_unavailable",
        "oidc_reauthentication_start_failed",
    }
    assert (
        client.get("/auth/oidc/provider", headers=auth_headers["viewer"]).status_code
        == 403
    )


def test_oidc_provider_update_supports_optimistic_revision(client, auth_headers):
    browser_headers = _local_admin_browser_headers(client)
    created = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(expected_config_revision=0),
        headers=browser_headers,
    )
    assert created.status_code == 200
    revision = created.json()["config_revision"]

    stale_create = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(
            name="Competing initial setup", expected_config_revision=0
        ),
        headers=browser_headers,
    )
    assert stale_create.status_code == 409
    assert stale_create.headers["X-Current-Version"] == str(revision)
    assert stale_create.json()["error"]["code"] == ("oidc_provider_revision_conflict")
    assert stale_create.json()["detail"] == {
        "message": (
            "OIDC provider settings changed after they were loaded. "
            "Reload the settings and apply your changes again."
        ),
        "expected_config_revision": 0,
        "current_config_revision": revision,
    }

    updated = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(name="Updated SSO", expected_config_revision=revision),
        headers=browser_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["config_revision"] == revision + 1

    stale = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(name="Stale update", expected_config_revision=revision),
        headers=browser_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "oidc_provider_revision_conflict"
    assert stale.headers["X-Current-Version"] == str(revision + 1)
    assert stale.json()["detail"]["current_config_revision"] == revision + 1


def test_oidc_provider_update_keeps_omitted_revision_legacy_compatible(
    client, auth_headers
):
    browser_headers = _local_admin_browser_headers(client)

    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers=browser_headers,
    )

    assert response.status_code == 200
    assert response.json()["config_revision"] == 1


def test_concurrent_oidc_provider_compare_and_create_has_one_winner(
    database_engine,
):
    admin_id = uuid.uuid4()
    with Session(database_engine) as setup_db:
        admin = User(
            id=admin_id,
            email=f"oidc-compare-create-{admin_id}@example.com",
            password_hash=get_password_hash("ConcurrentAdminPass123!"),
            role="admin",
            is_active=True,
            is_approved=True,
        )
        setup_db.add(admin)
        setup_db.commit()
        first_session = create_auth_session(
            setup_db,
            user_id=admin_id,
            auth_method="local",
            mfa_method=None,
            client_ip="198.51.100.31",
            user_agent="concurrent-provider-one",
        )
        setup_db.commit()
        second_session = create_auth_session(
            setup_db,
            user_id=admin_id,
            auth_method="local",
            mfa_method=None,
            client_ip="198.51.100.32",
            user_agent="concurrent-provider-two",
        )
        setup_db.commit()
        first_session_id = first_session.session.id
        second_session_id = second_session.session.id

    start = Event()

    def _request(token: str, session_id: uuid.UUID) -> Request:
        cookie_name = get_settings().auth_cookie_name
        request = Request(
            {
                "type": "http",
                "method": "PUT",
                "path": "/auth/oidc/provider",
                "headers": [(b"cookie", f"{cookie_name}={token}".encode("ascii"))],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
            }
        )
        request.state.auth_credential_kind = AUTH_SESSION_COOKIE
        request.state.auth_session_id = session_id
        return request

    payload = OIDCProviderUpdateRequest(
        **_provider_payload(
            expected_config_revision=0,
            client_auth_method="none",
            client_secret=None,
        )
    )

    def _configure_provider(token: str, session_id: uuid.UUID) -> tuple:
        with Session(database_engine) as update_db:
            assert start.wait(timeout=3)
            admin = update_db.get(User, admin_id)
            assert admin is not None
            try:
                provider = oidc_provider_routes.update_oidc_provider(
                    payload,
                    _request(token, session_id),
                    db=update_db,
                    admin=admin,
                    _scope_user=admin,
                )
            except ApiHTTPException as exc:
                update_db.rollback()
                return (
                    "conflict",
                    exc.status_code,
                    exc.error_code,
                    exc.headers,
                    exc.detail,
                )
            return ("success", provider.config_revision)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                _configure_provider,
                first_session.token,
                first_session_id,
            )
            second = executor.submit(
                _configure_provider,
                second_session.token,
                second_session_id,
            )
            start.set()
            results = [first.result(timeout=5), second.result(timeout=5)]

        assert [result[0] for result in results].count("success") == 1
        assert [result[0] for result in results].count("conflict") == 1
        assert next(result for result in results if result[0] == "success") == (
            "success",
            1,
        )
        conflict = next(result for result in results if result[0] == "conflict")
        assert conflict[1:4] == (
            409,
            "oidc_provider_revision_conflict",
            {"X-Current-Version": "1"},
        )
        assert conflict[4]["expected_config_revision"] == 0
        assert conflict[4]["current_config_revision"] == 1
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(OIDCProvider).where(OIDCProvider.system_key == "primary")
            )
            cleanup_db.execute(delete(User).where(User.id == admin_id))
            cleanup_db.commit()


def test_oidc_provider_identity_key_cannot_change_after_link(
    client, auth_headers, db_session, seed_users
):
    browser_headers = _local_admin_browser_headers(client)
    provider = _configured_provider(db_session)
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=seed_users["analyst"].id,
            issuer=provider.issuer_url,
            subject="subject-1",
            email_at_link=seed_users["analyst"].email,
        )
    )
    db_session.commit()

    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(issuer_url="https://different-idp.example.com"),
        headers=browser_headers,
    )

    assert response.status_code == 409
    assert "cannot change" in response.json()["detail"]


def test_oidc_provider_update_accepts_properly_scoped_admin_api_token(
    client,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    headers = _api_token_headers(
        db_session,
        admin,
        scopes=["write:users"],
    )
    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["config_revision"] == 1
    provider = db_session.scalar(
        select(OIDCProvider).where(OIDCProvider.system_key == "primary")
    )
    assert provider is not None
    assert provider.updated_by_user_id == admin.id
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "oidc.provider.update",
            AuditLog.resource_id == str(provider.id),
        )
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.metadata_json["config_revision"] == 1

    stale = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(
            name="Stale API client update",
            expected_config_revision=0,
        ),
        headers=headers,
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "oidc_provider_revision_conflict"
    assert stale.json()["detail"] == {
        "message": (
            "OIDC provider settings changed after they were loaded. "
            "Reload the settings and apply your changes again."
        ),
        "expected_config_revision": 0,
        "current_config_revision": 1,
    }
    assert stale.headers["x-current-version"] == "1"


@pytest.mark.parametrize(
    ("user_key", "scopes", "expected_detail"),
    [
        ("admin", ["read:users"], "Insufficient token scope"),
        ("analyst", ["write:users"], "Insufficient permissions"),
    ],
)
def test_oidc_provider_update_rejects_under_scoped_or_non_admin_api_token(
    client,
    db_session,
    seed_users,
    user_key,
    scopes,
    expected_detail,
):
    headers = _api_token_headers(
        db_session,
        seed_users[user_key],
        scopes=scopes,
    )

    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(expected_config_revision=0),
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == expected_detail
    assert (
        db_session.scalar(
            select(OIDCProvider).where(OIDCProvider.system_key == "primary")
        )
        is None
    )


def test_local_admin_can_step_up_then_mutate_oidc_provider(
    client,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    browser_headers = _local_admin_browser_headers(client)
    session_token = client.cookies.get("threatlens_session")
    assert session_token
    session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(session_token)
        )
    )
    assert session is not None
    session.authenticated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    stale = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers=browser_headers,
    )
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "local_reauthentication_required"
    assert stale.json()["error"]["context"]["reauthentication_endpoint"] == (
        "/auth/security/reauthenticate"
    )

    reauthenticated = client.post(
        "/auth/security/reauthenticate",
        json={"current_password": "AdminPass123!"},
        headers=browser_headers,
    )
    assert reauthenticated.status_code == 200, reauthenticated.text
    next_csrf = client.cookies.get("threatlens_csrf")
    assert next_csrf
    updated = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers={"X-CSRF-Token": next_csrf},
    )
    assert updated.status_code == 200, updated.text

    current_auth = client.get("/auth/me").json()["authentication"]
    assert current_auth["credential_kind"] == "opaque_session"
    assert current_auth["session_auth_method"] == "local"
    assert current_auth["recently_authenticated"] is True
    assert current_auth["recent_authentication_valid"] is True
    assert current_auth["reauthentication_endpoint"] == (
        "/auth/security/reauthenticate"
    )
    db_session.refresh(admin)


def test_mfa_enabled_local_admin_must_step_up_with_current_totp_for_provider_update(
    client,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    browser_headers = _local_admin_browser_headers(client)
    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers=browser_headers,
    )
    assert enrollment.status_code == 200, enrollment.text
    secret = enrollment.json()["secret"]
    confirmation = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers=browser_headers,
    )
    assert confirmation.status_code == 200, confirmation.text
    csrf = client.cookies.get("threatlens_csrf")
    session_token = client.cookies.get("threatlens_session")
    assert csrf and session_token
    current = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(session_token)
        )
    )
    credential = db_session.scalar(
        select(UserTOTPCredential).where(UserTOTPCredential.user_id == admin.id)
    )
    assert current is not None and credential is not None
    current.authenticated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    credential.last_accepted_step = None
    db_session.commit()

    stale = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "local_reauthentication_required"

    missing_totp = client.post(
        "/auth/security/reauthenticate",
        json={"current_password": "AdminPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_totp.status_code == 403
    assert missing_totp.json()["error"]["code"] == "mfa_verification_required"

    stepped_up = client.post(
        "/auth/security/reauthenticate",
        json={
            "current_password": "AdminPass123!",
            "code": pyotp.TOTP(secret).now(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert stepped_up.status_code == 200, stepped_up.text
    assert stepped_up.json()["verification_method"] == "password_totp"

    next_csrf = client.cookies.get("threatlens_csrf")
    assert next_csrf
    updated = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(),
        headers={"X-CSRF-Token": next_csrf},
    )
    assert updated.status_code == 200, updated.text


def test_hybrid_oidc_admin_uses_actual_session_method_for_provider_step_up(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    provider, session_token = _linked_oidc_session(db_session, admin)
    provider_id = provider.id
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "reauth-subject",
            "email": admin.email,
            "email_verified": True,
            "amr": ["pwd", "mfa"],
        },
    )
    monkeypatch.setattr(
        oidc_routes,
        "acquire_active_admin_invariant_lock",
        lambda _db: pytest.fail(
            "OIDC reauthentication must not take the role-sync invariant lock"
        ),
    )
    client.cookies.set(
        "threatlens_session",
        session_token,
        domain="testserver.local",
    )
    client.cookies.set(
        "threatlens_csrf",
        "oidc-admin-csrf",
        domain="testserver.local",
    )

    rejected = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(expected_config_revision=provider.config_revision),
        headers={"X-CSRF-Token": "oidc-admin-csrf"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "oidc_reauthentication_required"
    assert rejected.json()["error"]["context"]["reauthentication_endpoint"] == (
        "/auth/oidc/reauth"
    )

    start = client.post(
        "/auth/oidc/reauth",
        headers={"X-CSRF-Token": "oidc-admin-csrf"},
    )
    assert start.status_code == 200, start.text
    assert start.json()["mode"] == "reauth"
    assert start.json()["result_query_parameter"] == "oidc_reauth"
    assert start.json()["success_code"] == "success"
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.headers["location"].endswith("oidc_reauth=success")

    current_auth = client.get("/auth/me").json()["authentication"]
    assert current_auth["session_auth_method"] == "oidc"
    assert current_auth["identity_provider_mfa_asserted"] is True
    assert current_auth["reauthentication_endpoint"] == "/auth/oidc/reauth"

    provider = db_session.get(OIDCProvider, provider_id)
    assert provider is not None
    csrf = client.cookies.get("threatlens_csrf", domain="testserver.local")
    assert csrf
    updated = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text


def test_oidc_provider_update_requires_configured_external_mfa_assurance(
    client,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    provider = _configured_provider(db_session)
    created = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=int(admin.auth_token_version or 0),
        auth_method="oidc",
        mfa_method=None,
        identity_authenticated_at=datetime.now(timezone.utc),
        client_ip="testclient",
        user_agent="pytest plain oidc assurance",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token)
    client.cookies.set("threatlens_csrf", "plain-oidc-csrf")

    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
        headers={"X-CSRF-Token": "plain-oidc-csrf"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "oidc_mfa_assurance_required"
    db_session.refresh(provider)
    assert provider.config_revision == 1


def test_oidc_cannot_be_disabled_without_a_local_break_glass_admin(
    client,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    admin.password_login_enabled = False
    admin.provisioning_source = "oidc"
    provider = _configured_provider(db_session)
    created = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=int(admin.auth_token_version or 0),
        auth_method="oidc",
        mfa_method="external",
        identity_authenticated_at=datetime.now(timezone.utc),
        identity_amr=["pwd", "mfa"],
        client_ip="testclient",
        user_agent="pytest break glass",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token)
    client.cookies.set("threatlens_csrf", "break-glass-csrf")

    response = client.put(
        "/auth/oidc/provider",
        json=_provider_payload(
            enabled=False,
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
        headers={"X-CSRF-Token": "break-glass-csrf"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "oidc_break_glass_admin_required"
    db_session.expire_all()
    assert db_session.get(OIDCProvider, provider.id).enabled is True
    rejected_audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "oidc.provider.update",
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejected_audit is not None
    assert rejected_audit.metadata_json["reason"] == (
        "local_break_glass_admin_required"
    )


def test_disabling_oidc_provider_revokes_all_linked_credentials(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    provider = _configured_provider(db_session)
    viewer = seed_users["viewer"]
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=viewer.id,
        issuer=provider.issuer_url,
        subject="provider-disable-viewer",
        email_at_link=viewer.email,
        role_sync_provenance="legacy",
        role_sync_applied_role=viewer.role,
        role_sync_updated_at=datetime.now(timezone.utc),
    )
    db_session.add(identity)
    oidc_session = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_token_version=int(viewer.auth_token_version or 0),
        auth_method="oidc",
        mfa_method=None,
        client_ip="testclient",
        user_agent="pytest provider disable oidc",
    ).session
    local_session = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_token_version=int(viewer.auth_token_version or 0),
        auth_method="local",
        mfa_method=None,
        client_ip="testclient",
        user_agent="pytest provider disable local",
    ).session
    db_session.commit()

    response = client.put(
        "/auth/oidc/provider",
        headers=auth_headers["admin"],
        json=_provider_payload(
            enabled=False,
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(AuthSession, oidc_session.id).revoked_at is not None
    assert db_session.get(AuthSession, local_session.id).revoked_at is not None
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.provider.update", AuditLog.success.is_(True))
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["purge_trigger"] == "provider_disabled"
    assert audit.metadata_json["linked_user_count"] == 1
    assert audit.metadata_json["revoked_oidc_sessions"] == 1
    assert audit.metadata_json["legacy_role_retained"] is True

    db_session.refresh(viewer)
    confirmed = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={
            "role": viewer.role,
            "expected_security_version": viewer.auth_token_version,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    db_session.refresh(identity)
    assert identity.role_sync_provenance is None


def test_disabling_oidc_provider_restores_tracked_fixed_role(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    provider = _configured_provider(db_session)
    viewer = seed_users["viewer"]
    viewer.role = "analyst"
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=viewer.id,
        issuer=provider.issuer_url,
        subject="provider-disable-tracked-role",
        email_at_link=viewer.email,
        role_sync_provenance="tracked",
        role_sync_previous_role="viewer",
        role_sync_applied_role="analyst",
        role_sync_updated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([viewer, identity])
    db_session.commit()

    response = client.put(
        "/auth/oidc/provider",
        headers=auth_headers["admin"],
        json=_provider_payload(
            enabled=False,
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, viewer.id).role == "viewer"
    restored_identity = db_session.get(ExternalIdentity, identity.id)
    assert restored_identity.role_sync_provenance is None
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.provider.update", AuditLog.success.is_(True))
        .order_by(AuditLog.created_at.desc())
    )
    assert audit.metadata_json["fixed_roles_reverted"] == 1
    assert audit.metadata_json["fixed_roles_with_legacy_provenance"] == 0


def test_fixed_role_reversion_preserves_owner_with_durable_local_custom_access(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    provider = _configured_provider(db_session)
    viewer = seed_users["viewer"]
    viewer.role = "analyst"
    local_role = IAMRole(
        key=f"durable-investigation-owner-{uuid.uuid4().hex[:8]}",
        name="Durable investigation owner",
        description="Keeps ownership valid after fixed-role reversion.",
        is_system=False,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
    )
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=viewer.id,
        issuer=provider.issuer_url,
        subject="provider-disable-durable-owner",
        email_at_link=viewer.email,
        role_sync_provenance="tracked",
        role_sync_previous_role="viewer",
        role_sync_applied_role="analyst",
        role_sync_updated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([viewer, local_role, identity])
    db_session.flush()
    db_session.add_all(
        [
            IAMRolePermission(
                role_id=local_role.id,
                permission="write:investigations",
            ),
            IAMUserRoleAssignment(
                user_id=viewer.id,
                role_id=local_role.id,
                source="local",
                source_key="",
                assigned_by_user_id=seed_users["admin"].id,
            ),
        ]
    )
    investigation = Investigation(
        title="Durably owned investigation",
        description="Local custom access survives provider disable.",
        severity="medium",
        visibility="private",
        created_by_user_id=viewer.id,
    )
    db_session.add(investigation)
    db_session.flush()
    db_session.add(
        InvestigationMember(
            investigation_id=investigation.id,
            user_id=viewer.id,
            role="owner",
            added_by_user_id=viewer.id,
        )
    )
    db_session.commit()

    response = client.put(
        "/auth/oidc/provider",
        headers=auth_headers["admin"],
        json=_provider_payload(
            enabled=False,
            expected_config_revision=provider.config_revision,
            client_auth_method="none",
            client_secret=None,
        ),
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(User, viewer.id).role == "viewer"
    owner = db_session.get(
        InvestigationMember,
        {"investigation_id": investigation.id, "user_id": viewer.id},
    )
    assert owner is not None
    assert owner.role == "owner"


def test_rate_limited_valid_reauthentication_callback_uses_reauth_contract(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    _provider, session_token = _linked_oidc_session(db_session, user)
    _mock_oidc_flow(monkeypatch, {"sub": "reauth-subject"})
    client.cookies.set("threatlens_session", session_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "rate-csrf", domain="testserver.local")
    start = client.post(
        "/auth/oidc/reauth",
        headers={"X-CSRF-Token": "rate-csrf"},
    )
    assert start.status_code == 200
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    monkeypatch.setattr(
        "app.api.routes.oidc.check_oidc_callback_throttle",
        lambda _ip: SimpleNamespace(blocked=True),
    )

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"].endswith("oidc_reauth=callback_rate_limited")


def test_oidc_reauthentication_start_returns_coded_provider_unavailable_error(
    client,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    provider, session_token = _linked_oidc_session(db_session, user)
    provider.enabled = False
    db_session.commit()
    client.cookies.set("threatlens_session", session_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "reauth-csrf", domain="testserver.local")

    response = client.post(
        "/auth/oidc/reauth",
        headers={"X-CSRF-Token": "reauth-csrf"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "oidc_provider_unavailable"
    assert response.json()["error"]["context"]["reauthentication_method"] == "oidc"


def test_oidc_reauthentication_start_returns_coded_provider_failure(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    _provider, session_token = _linked_oidc_session(db_session, user)
    client.cookies.set("threatlens_session", session_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "reauth-csrf", domain="testserver.local")

    def fail_metadata(_provider):
        raise OIDCProtocolError("provider temporarily offline")

    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", fail_metadata)
    response = client.post(
        "/auth/oidc/reauth",
        headers={"X-CSRF-Token": "reauth-csrf"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oidc_reauthentication_start_failed"
    assert response.json()["error"]["retryable"] is True


def test_oidc_provider_connection_test_records_verified_metadata(
    client, auth_headers, db_session, monkeypatch
):
    provider = _configured_provider(db_session)
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("none",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    monkeypatch.setattr(
        "app.api.routes.oidc_provider.test_oidc_provider",
        lambda _provider: (metadata, 2),
    )

    response = client.post("/auth/oidc/provider/test", headers=auth_headers["admin"])

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "issuer": provider.issuer_url,
        "authorization_endpoint": metadata.authorization_endpoint,
        "token_endpoint": metadata.token_endpoint,
        "jwks_key_count": 2,
    }
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "oidc.provider.test")
    )
    assert audit is not None
    assert audit.success is True
    assert audit.metadata_json["jwks_key_count"] == 2


def test_oidc_provider_connection_test_records_actionable_failure(
    client, auth_headers, db_session, monkeypatch
):
    provider = _configured_provider(db_session)

    def fail_test(_provider):
        raise OIDCProtocolError("OIDC endpoint hostname could not be resolved")

    monkeypatch.setattr("app.api.routes.oidc_provider.test_oidc_provider", fail_test)

    response = client.post("/auth/oidc/provider/test", headers=auth_headers["admin"])

    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC endpoint hostname could not be resolved"
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "oidc.provider.test")
    )
    assert audit is not None
    assert audit.resource_id == str(provider.id)
    assert audit.success is False
    assert audit.metadata_json == {
        "error_type": "OIDCProtocolError",
        "reason": "OIDC endpoint hostname could not be resolved",
    }


def test_oidc_jit_login_provisions_verified_user_and_maps_role(
    client, db_session, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "subject-1",
            "email": "new-user@example.com",
            "email_verified": True,
            "groups": ["soc"],
        },
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://testserver/start"
    user = db_session.scalar(select(User).where(User.email == "new-user@example.com"))
    assert user is not None
    assert user.role == "analyst"
    assert user.password_login_enabled is False
    assert user.provisioning_source == "oidc"
    assert user.is_approved is True
    identity = db_session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)
    )
    assert identity is not None
    assert identity.subject == "subject-1"
    assert client.get("/auth/me").status_code == 200


def test_oidc_jit_pending_user_is_created_without_a_session(
    client, db_session, monkeypatch
):
    _configured_provider(db_session, auto_approve_users=False)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "pending-subject",
            "email": "pending@example.com",
            "email_verified": True,
            "groups": [],
        },
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "approval_required"
    ]
    user = db_session.scalar(select(User).where(User.email == "pending@example.com"))
    assert user is not None
    assert user.is_approved is False
    assert user.password_login_enabled is False
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)
        )
        is not None
    )
    assert client.get("/auth/me").status_code == 401


def test_oidc_jit_rejects_unverified_email_without_creating_user(
    client, db_session, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "subject-1",
            "email": "new-user@example.com",
            "email_verified": False,
            "groups": [],
        },
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "verified_email_required"
    ]
    assert (
        db_session.scalar(select(User).where(User.email == "new-user@example.com"))
        is None
    )


def test_oidc_jit_can_accept_unverified_email_only_when_provider_policy_allows_it(
    client,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session, require_verified_email=False)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "subject-1",
            "email": "trusted@example.com",
            "email_verified": False,
            "groups": [],
        },
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://testserver/start"
    user = db_session.scalar(select(User).where(User.email == "trusted@example.com"))
    assert user is not None
    assert user.is_approved is True
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)
        )
        is not None
    )


def test_oidc_jit_accepts_internal_email_identifier_when_verification_is_optional(
    client,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session, require_verified_email=False)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "internal-subject",
            "email": "Admin@Admin.Local",
            "email_verified": False,
            "groups": [],
        },
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://testserver/start"
    user = db_session.scalar(select(User).where(User.email == "admin@admin.local"))
    assert user is not None
    identity = db_session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)
    )
    assert identity is not None
    assert identity.email_at_link == "admin@admin.local"


def test_oidc_jit_rejects_internal_email_identifier_when_strict_verification_is_enabled(
    client,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session, require_verified_email=True)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "internal-subject",
            "email": "admin@admin.local",
            "email_verified": True,
            "groups": [],
        },
    )

    callback = _start_and_complete(client)

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "invalid_email"
    ]
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.subject == "internal-subject"
            )
        )
        is None
    )


@pytest.mark.parametrize(
    (
        "claims",
        "expected_error",
        "email_claim_present",
        "email_value_present",
        "email_claim_type",
    ),
    [
        (
            {"sub": "subject-1", "email_verified": False},
            "email_required",
            False,
            False,
            None,
        ),
        (
            {"sub": "subject-1", "email": None, "email_verified": False},
            "email_required",
            True,
            False,
            None,
        ),
        (
            {"sub": "subject-1", "email": "", "email_verified": False},
            "email_required",
            True,
            False,
            "str",
        ),
        (
            {"sub": "subject-1", "email": "not-an-email", "email_verified": False},
            "invalid_email",
            True,
            True,
            "str",
        ),
        (
            {
                "sub": "subject-1",
                "email": "bad..local@internal.local",
                "email_verified": False,
            },
            "invalid_email",
            True,
            True,
            "str",
        ),
        (
            {
                "sub": "subject-1",
                "email": "admin@-internal.local",
                "email_verified": False,
            },
            "invalid_email",
            True,
            True,
            "str",
        ),
    ],
)
def test_oidc_jit_still_rejects_missing_or_invalid_email_when_verification_is_optional(
    client,
    db_session,
    monkeypatch,
    claims,
    expected_error,
    email_claim_present,
    email_value_present,
    email_claim_type,
):
    _configured_provider(db_session, require_verified_email=False)
    _mock_oidc_flow(monkeypatch, claims)

    callback = _start_and_complete(client)

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        expected_error
    ]
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.subject == "subject-1")
        )
        is None
    )
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.oidc.callback")
    )
    assert audit is not None
    assert audit.metadata_json["error_code"] == expected_error
    assert audit.metadata_json["claim_diagnostics"] == {
        "claims_available": True,
        "email_claim_present": email_claim_present,
        "email_value_present": email_value_present,
        "email_claim_type": email_claim_type,
        "email_verified_claim_present": True,
        "email_verified": False,
        "email_verified_claim_type": "bool",
    }


def test_oidc_jit_requires_explicit_link_for_existing_email(
    client, db_session, seed_users, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "subject-1",
            "email": seed_users["analyst"].email,
            "email_verified": True,
            "groups": ["soc"],
        },
    )

    callback = _start_and_complete(client)

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "email_link_required"
    ]
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.subject == "subject-1")
        )
        is None
    )


def test_oidc_link_and_unlink_flow_binds_identity_to_initiating_browser_session(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "linked-subject",
            "email": seed_users["analyst"].email,
            "email_verified": True,
            "groups": [],
        },
    )
    login = client.post(
        "/auth/login",
        json={"email": seed_users["analyst"].email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    initiating_session = client.cookies.get("threatlens_session")
    assert initiating_session

    callback = _start_and_complete(client, start_path="/auth/oidc/link")

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == [
        "success"
    ]
    identity = db_session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.provider_id == provider.id)
    )
    assert identity is not None
    assert identity.user_id == seed_users["analyst"].id
    rotated_session = client.cookies.get("threatlens_session")
    assert rotated_session and rotated_session != initiating_session
    client.cookies.set("threatlens_session", initiating_session)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", rotated_session)

    directory = client.get("/users", headers=auth_headers["admin"])
    entry = next(
        user for user in directory.json() if user["id"] == str(seed_users["analyst"].id)
    )
    assert entry["provisioning_source"] == "local"
    assert entry["authentication_methods"] == ["password", "oidc"]
    assert entry["password_managed_by"] == "local"
    assert entry["role_managed_by"] == "oidc"

    csrf_token = client.cookies.get("threatlens_csrf")
    assert csrf_token
    unlinked = client.request(
        "DELETE",
        "/auth/oidc/account",
        json={"current_password": "AnalystPass123!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert unlinked.status_code == 204
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.id == identity.id)
        )
        is None
    )


def test_oidc_unlink_requires_browser_session(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    provider = _configured_provider(db_session)
    analyst = seed_users["analyst"]
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=analyst.id,
        issuer=provider.issuer_url,
        subject="api-token-unlink",
        email_at_link=analyst.email,
    )
    db_session.add(identity)
    db_session.commit()

    response = client.request(
        "DELETE",
        "/auth/oidc/account",
        headers=auth_headers["analyst"],
        json={"current_password": "AnalystPass123!"},
    )

    assert response.status_code == 403
    assert "browser session" in response.json()["detail"]
    db_session.refresh(identity)


def test_oidc_unlink_requires_local_mfa_and_rotates_browser_sessions(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "mfa-unlink-subject"})
    analyst = seed_users["analyst"]
    login = client.post(
        "/auth/login",
        json={"email": analyst.email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    linked = _start_and_complete(client, start_path="/auth/oidc/link")
    assert parse_qs(urlsplit(linked.headers["location"]).query)["oidc_link"] == [
        "success"
    ]

    csrf = client.cookies.get("threatlens_csrf")
    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AnalystPass123!"},
        headers={"x-csrf-token": csrf},
    )
    assert enrollment.status_code == 200, enrollment.text
    confirmation = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(enrollment.json()["secret"]).now()},
        headers={"x-csrf-token": csrf},
    )
    assert confirmation.status_code == 200, confirmation.text
    recovery_code = confirmation.json()["recovery_codes"][0]
    before_unlink_token = client.cookies.get("threatlens_session")
    csrf = client.cookies.get("threatlens_csrf")
    assert before_unlink_token and csrf
    db_session.refresh(analyst)
    other = create_auth_session(
        db_session,
        user_id=analyst.id,
        auth_token_version=analyst.auth_token_version,
        auth_method="local",
        mfa_method="totp",
        client_ip="198.51.100.40",
        user_agent="Other linked browser",
    )
    db_session.commit()

    missing_factor = client.request(
        "DELETE",
        "/auth/oidc/account",
        headers={"x-csrf-token": csrf},
        json={"current_password": "AnalystPass123!"},
    )
    assert missing_factor.status_code == 400
    assert "authenticator or recovery code" in missing_factor.json()["detail"]

    unlinked = client.request(
        "DELETE",
        "/auth/oidc/account",
        headers={"x-csrf-token": csrf},
        json={
            "current_password": "AnalystPass123!",
            "code": recovery_code,
        },
    )
    assert unlinked.status_code == 204, unlinked.text
    after_unlink_token = client.cookies.get("threatlens_session")
    assert after_unlink_token and after_unlink_token != before_unlink_token
    db_session.refresh(other.session)
    assert other.session.revoked_at is not None
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.subject == "mfa-unlink-subject"
            )
        )
        is None
    )
    replacement = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(after_unlink_token)
        )
    )
    assert replacement is not None
    assert replacement.auth_method == "local"
    assert replacement.mfa_method == "recovery_code"
    client.cookies.set("threatlens_session", before_unlink_token)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", after_unlink_token)
    assert client.get("/auth/me").status_code == 200
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "oidc.identity.unlink")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["session_rotated"] is True
    assert audit.metadata_json["revoked_other_sessions"] >= 1


def test_oidc_link_start_requires_csrf_and_cookie_session(
    client, auth_headers, db_session, seed_users, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "linked-subject"})

    bearer_response = client.post("/auth/oidc/link", headers=auth_headers["analyst"])
    assert bearer_response.status_code == 400
    assert (
        bearer_response.json()["detail"]
        == "OIDC account linking requires a browser session"
    )

    login = client.post(
        "/auth/login",
        json={"email": seed_users["analyst"].email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    missing_csrf = client.post("/auth/oidc/link")
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"] == "Missing or invalid CSRF token"

    csrf_token = client.cookies.get("threatlens_csrf")
    missing_password = client.post(
        "/auth/oidc/link",
        headers={"X-CSRF-Token": csrf_token},
        json={},
    )
    assert missing_password.status_code == 400
    assert "current ThreatLens password" in missing_password.json()["detail"]


def test_oidc_login_remains_compatible_when_provider_omits_auth_time(
    client,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "stale-auth-subject",
            "email": "stale-auth@example.com",
            "email_verified": True,
            "auth_time": None,
        },
    )

    callback = _start_and_complete(client)

    assert callback.headers["location"] == "http://testserver/start"
    assert client.get("/auth/me").status_code == 200


def test_oidc_link_rejects_missing_recent_identity_provider_authentication(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "link-without-auth-time",
            "email": seed_users["analyst"].email,
            "email_verified": True,
            "auth_time": None,
        },
    )
    login = client.post(
        "/auth/login",
        json={"email": seed_users["analyst"].email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200

    callback = _start_and_complete(client, start_path="/auth/oidc/link")

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == [
        "reauthentication_failed"
    ]
    assert client.get("/auth/me").status_code == 200
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.subject == "link-without-auth-time"
            )
        )
        is None
    )


def test_oidc_callback_rejects_state_mismatch_and_clears_transaction_cookie(
    client, db_session, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "subject-1"})
    start = client.get("/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 302

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": "wrong-state", "code": "authorization-code"},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "invalid_state"
    ]
    assert "threatlens_oidc_transaction" not in client.cookies
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.oidc.callback")
    )
    assert audit is None


@pytest.mark.parametrize(
    ("callback_params", "error_code"),
    [
        ({"error": "access_denied"}, "provider_rejected"),
        ({}, "missing_code"),
    ],
)
def test_oidc_callback_handles_provider_rejection_and_missing_code(
    client,
    db_session,
    monkeypatch,
    callback_params,
    error_code,
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "subject-1"})
    start = client.get("/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, **callback_params},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        error_code
    ]
    assert "threatlens_oidc_transaction" not in client.cookies


def test_oidc_callback_records_protocol_failures_without_exposing_provider_details(
    client,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "subject-1"})

    def fail_exchange(*_args, **_kwargs):
        raise OIDCProtocolError("sensitive provider response")

    monkeypatch.setattr("app.api.routes.oidc.exchange_oidc_code", fail_exchange)

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    location = callback.headers["location"]
    assert parse_qs(urlsplit(location).query)["oidc_error"] == ["authentication_failed"]
    assert "sensitive" not in location
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.oidc.callback")
    )
    assert audit is not None
    assert audit.metadata_json["error_code"] == "authentication_failed"


def test_oidc_login_start_returns_stable_service_error_and_audit_when_discovery_fails(
    client,
    db_session,
    monkeypatch,
):
    provider = _configured_provider(db_session)

    def fail_metadata(_provider):
        raise OIDCProtocolError("provider offline")

    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", fail_metadata)

    response = client.get("/auth/oidc/login", follow_redirects=False)

    assert response.status_code == 503
    assert response.json()["detail"] == "OIDC sign-in could not start: provider offline"
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.oidc.start")
    )
    assert audit is not None
    assert audit.resource_id == str(provider.id)
    assert audit.success is False
    assert audit.metadata_json == {
        "mode": "login",
        "error_type": "OIDCProtocolError",
        "reason": "provider offline",
    }


def test_oidc_browser_login_returns_to_login_page_when_discovery_fails(
    client,
    db_session,
    monkeypatch,
):
    provider = _configured_provider(db_session)

    def fail_metadata(_provider):
        raise OIDCProtocolError("provider offline")

    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", fail_metadata)

    response = client.get(
        "/auth/oidc/login",
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "http://testserver/login?oidc_error=provider_unavailable"
    )
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "auth.oidc.start")
    )
    assert audit is not None
    assert audit.resource_id == str(provider.id)
    assert audit.metadata_json["reason"] == "provider offline"


def test_oidc_login_rejects_inactive_linked_account_without_a_session(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(
        db_session, jit_provisioning_enabled=False, sync_roles_on_login=False
    )
    analyst = seed_users["analyst"]
    analyst.is_active = False
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=analyst.id,
            issuer=provider.issuer_url,
            subject="inactive-subject",
            email_at_link=analyst.email,
        )
    )
    db_session.commit()
    _mock_oidc_flow(monkeypatch, {"sub": "inactive-subject"})

    callback = _start_and_complete(client)

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "account_inactive"
    ]
    assert client.get("/auth/me").status_code == 401
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "auth.oidc.login", AuditLog.success.is_(False)
        )
    )
    assert audit is not None
    assert audit.metadata_json["error_code"] == "account_inactive"


def test_oidc_login_without_role_sync_does_not_take_admin_invariant_lock(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(
        db_session,
        jit_provisioning_enabled=False,
        sync_roles_on_login=False,
    )
    viewer = seed_users["viewer"]
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=viewer.id,
            issuer=provider.issuer_url,
            subject="no-role-sync-subject",
            email_at_link=viewer.email,
        )
    )
    db_session.commit()
    _mock_oidc_flow(monkeypatch, {"sub": "no-role-sync-subject"})
    monkeypatch.setattr(
        oidc_routes,
        "acquire_active_admin_invariant_lock",
        lambda _db: pytest.fail(
            "A login with role synchronization disabled must not take the admin lock"
        ),
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert "oidc_error" not in parse_qs(urlsplit(callback.headers["location"]).query)
    assert client.get("/auth/me").status_code == 200


def test_oidc_role_sync_preserves_last_admin_and_allows_login(
    client, db_session, seed_users, monkeypatch
):
    provider = _configured_provider(
        db_session,
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
    )
    admin = seed_users["admin"]
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=admin.id,
        issuer=provider.issuer_url,
        subject="admin-subject",
        email_at_link=admin.email,
    )
    db_session.add(identity)
    db_session.commit()
    _mock_oidc_flow(monkeypatch, {"sub": "admin-subject", "groups": []})

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert "oidc_error" not in parse_qs(urlsplit(callback.headers["location"]).query)
    db_session.refresh(admin)
    assert admin.role == "admin"
    assert client.get("/auth/me").status_code == 200
    blocked_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "oidc.role.sync", AuditLog.success.is_(False)
        )
    )
    assert blocked_audit is not None
    assert blocked_audit.actor_user_id == admin.id
    assert blocked_audit.metadata_json["reason"] == "last_active_admin"
    assert (
        db_session.scalar(
            select(ApiToken).where(
                ApiToken.user_id == admin.id, ApiToken.revoked_at.is_not(None)
            )
        )
        is None
    )


def test_oidc_role_sync_rejects_login_when_demotion_would_orphan_investigation(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(
        db_session,
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
    )
    analyst = seed_users["analyst"]
    investigation = client.post(
        "/investigations",
        headers=auth_headers["analyst"],
        json={"title": "OIDC ownership invariant", "visibility": "private"},
    )
    assert investigation.status_code == 201, investigation.text
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=analyst.id,
            issuer=provider.issuer_url,
            subject="investigation-owner-subject",
            email_at_link=analyst.email,
        )
    )
    db_session.commit()
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "investigation-owner-subject", "groups": []},
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == [
        "role_sync_blocked"
    ]
    db_session.refresh(analyst)
    assert analyst.role == "analyst"
    assert client.get("/auth/me").status_code == 401
    blocked_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "auth.oidc.callback",
            AuditLog.success.is_(False),
        )
    )
    assert blocked_audit is not None
    assert blocked_audit.actor_user_id == analyst.id
    assert (
        blocked_audit.metadata_json["role_sync_reason"]
        == "investigation_owner_reassignment_required"
    )
    assert blocked_audit.metadata_json["affected_investigation_count"] == 1


def test_oidc_role_sync_revokes_existing_sessions_and_api_tokens(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(
        db_session,
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
    )
    analyst = seed_users["analyst"]
    previous_token_version = analyst.auth_token_version
    db_session.add(
        ExternalIdentity(
            provider_id=provider.id,
            user_id=analyst.id,
            issuer=provider.issuer_url,
            subject="analyst-subject",
            email_at_link=analyst.email,
        )
    )
    db_session.commit()
    _mock_oidc_flow(monkeypatch, {"sub": "analyst-subject", "groups": []})

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    db_session.refresh(analyst)
    assert analyst.role == "viewer"
    assert analyst.auth_token_version == previous_token_version + 1
    revoked_tokens = db_session.scalars(
        select(ApiToken).where(
            ApiToken.user_id == analyst.id, ApiToken.revoked_at.is_not(None)
        )
    ).all()
    assert len(revoked_tokens) == 1
    assert client.get("/auth/me", headers=auth_headers["analyst"]).status_code == 401
    sync_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "oidc.role.sync", AuditLog.success.is_(True)
        )
    )
    assert sync_audit is not None
    assert sync_audit.metadata_json["previous_role"] == "analyst"
    assert sync_audit.metadata_json["role"] == "viewer"
    assert sync_audit.metadata_json["revoked_api_tokens"] == 1


def test_oidc_role_sync_never_promotes_legacy_provenance_automatically(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    provider = _configured_provider(
        db_session,
        role_mappings_json=[],
        default_role="viewer",
        jit_provisioning_enabled=False,
    )
    analyst = seed_users["analyst"]
    identity = ExternalIdentity(
        provider_id=provider.id,
        user_id=analyst.id,
        issuer=provider.issuer_url,
        subject="legacy-role-provenance",
        email_at_link=analyst.email,
        role_sync_provenance="legacy",
        role_sync_applied_role="analyst",
        role_sync_updated_at=datetime.now(timezone.utc),
    )
    db_session.add(identity)
    db_session.commit()
    _mock_oidc_flow(monkeypatch, {"sub": identity.subject, "groups": []})

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    db_session.refresh(identity)
    db_session.refresh(analyst)
    assert analyst.role == "viewer"
    assert identity.role_sync_provenance == "legacy"
    assert identity.role_sync_previous_role is None
    assert identity.role_sync_applied_role == "viewer"


def test_oidc_only_account_cannot_use_local_login_or_unlink(
    client, db_session, monkeypatch
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "external-only",
            "email": "external-only@example.com",
            "email_verified": True,
            "groups": [],
        },
    )
    callback = _start_and_complete(client)
    assert callback.headers["location"] == "http://testserver/start"

    local_login = client.post(
        "/auth/login",
        json={"email": "external-only@example.com", "password": "unavailable-password"},
    )
    assert local_login.status_code == 401
    assert local_login.json()["detail"] == "Invalid email or password"

    csrf_token = client.cookies.get("threatlens_csrf")
    assert csrf_token
    unlink = client.request(
        "DELETE",
        "/auth/oidc/account",
        json={"current_password": "unavailable-password"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert unlink.status_code == 400
    assert (
        unlink.json()["detail"]
        == "SSO-provisioned accounts cannot unlink their managed sign-in identity"
    )
    identity = db_session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.subject == "external-only")
    )
    assert identity is not None


def test_oidc_link_callback_rejects_a_session_revoked_after_link_start(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "linked-subject"})
    analyst = seed_users["analyst"]
    login = client.post(
        "/auth/login",
        json={"email": analyst.email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    csrf_token = client.cookies.get("threatlens_csrf")
    start = client.post(
        "/auth/oidc/link",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": "AnalystPass123!"},
    )
    assert start.status_code == 200, start.text
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]

    analyst.auth_token_version += 1
    db_session.add(analyst)
    db_session.commit()

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == [
        "link_session_expired"
    ]
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(ExternalIdentity.subject == "linked-subject")
        )
        is None
    )


def test_oidc_link_callback_serializes_with_concurrent_credential_rotation(
    database_engine,
    monkeypatch,
    _install_test_redis_backend,
):
    _ = _install_test_redis_backend
    user_id = uuid.uuid4()
    subject = f"concurrent-link-{user_id}"
    resolver_started = Event()

    with Session(database_engine) as setup_db:
        provider = _configured_provider(setup_db)
        provider_id = provider.id
        user = User(
            id=user_id,
            email=f"{subject}@example.com",
            password_hash=get_password_hash("AnalystPass123!"),
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        setup_db.add(user)
        setup_db.flush()
        created = create_auth_session(
            setup_db,
            user_id=user_id,
            auth_token_version=0,
            auth_method="local",
            mfa_method=None,
            client_ip="192.0.2.30",
            user_agent="OIDC race test",
        )
        session_token = created.token
        setup_db.commit()

    def _request_db():
        with Session(database_engine) as request_db:
            yield request_db

    app.dependency_overrides[get_db] = _request_db
    _mock_oidc_flow(monkeypatch, {"sub": subject})
    original_resolver = oidc_routes._resolve_bound_opaque_session_user

    def _tracked_resolver(*args, **kwargs):
        resolver_started.set()
        return original_resolver(*args, **kwargs)

    monkeypatch.setattr(
        oidc_routes, "_resolve_bound_opaque_session_user", _tracked_resolver
    )
    try:
        with TestClient(app) as isolated_client:
            isolated_client.cookies.set("threatlens_session", session_token)
            isolated_client.cookies.set("threatlens_csrf", "oidc-race-csrf")
            start = isolated_client.post(
                "/auth/oidc/link",
                headers={"X-CSRF-Token": "oidc-race-csrf"},
                json={"current_password": "AnalystPass123!"},
            )
            assert start.status_code == 200, start.text
            state = parse_qs(urlsplit(start.json()["authorization_url"]).query)[
                "state"
            ][0]

            def _complete_callback():
                return isolated_client.get(
                    "/auth/oidc/callback",
                    params={"state": state, "code": "authorization-code"},
                    follow_redirects=False,
                )

            with Session(database_engine) as access_db:
                locked_user = load_user_for_access_update(access_db, user_id)
                assert locked_user is not None
                locked_user.auth_token_version += 1
                with ThreadPoolExecutor(max_workers=1) as executor:
                    result = executor.submit(_complete_callback)
                    assert resolver_started.wait(timeout=2)
                    assert not result.done()
                    access_db.commit()
                    callback = result.result(timeout=3)

        assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == [
            "link_session_expired"
        ]
        with Session(database_engine) as verification_db:
            assert (
                verification_db.scalar(
                    select(ExternalIdentity).where(ExternalIdentity.subject == subject)
                )
                is None
            )
            user = verification_db.get(User, user_id)
            assert user is not None and user.auth_token_version == 1
            assert (
                verification_db.scalar(
                    select(AuthSession.id).where(
                        AuthSession.user_id == user_id,
                        AuthSession.auth_token_version == 1,
                        AuthSession.revoked_at.is_(None),
                    )
                )
                is None
            )
    finally:
        app.dependency_overrides.clear()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.execute(
                delete(OIDCProvider).where(OIDCProvider.id == provider_id)
            )
            cleanup_db.commit()


def test_oidc_link_callback_is_bound_to_the_exact_initiating_session(
    client,
    db_session,
    seed_users,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "exact-session-subject"})
    analyst = seed_users["analyst"]
    login = client.post(
        "/auth/login",
        json={"email": analyst.email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    csrf_token = client.cookies.get("threatlens_csrf")
    start = client.post(
        "/auth/oidc/link",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": "AnalystPass123!"},
    )
    assert start.status_code == 200, start.text
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]

    replacement = create_auth_session(
        db_session,
        user_id=analyst.id,
        auth_token_version=analyst.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip="testclient",
        user_agent="replacement session",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", replacement.token)

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == [
        "link_session_expired"
    ]
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.subject == "exact-session-subject"
            )
        )
        is None
    )


def test_sso_provisioned_account_rejects_locally_managed_identity_changes(
    client,
    auth_headers,
    db_session,
    monkeypatch,
):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "recovery-subject",
            "email": "recovery@example.com",
            "email_verified": True,
            "groups": [],
        },
    )
    callback = _start_and_complete(client)
    assert callback.headers["location"] == "http://testserver/start"
    user = db_session.scalar(select(User).where(User.email == "recovery@example.com"))
    assert user is not None
    assert user.password_login_enabled is False
    assert user.provisioning_source == "oidc"

    reset = client.patch(
        f"/users/{user.id}",
        json={"password": "RecoveryPass123!"},
        headers=auth_headers["admin"],
    )
    assert reset.status_code == 409
    assert (
        reset.json()["detail"]
        == "Password is managed by Acme SSO for this SSO-provisioned account"
    )

    role_update = client.patch(
        f"/users/{user.id}",
        json={
            "role": "analyst",
            "expected_security_version": user.auth_token_version,
        },
        headers=auth_headers["admin"],
    )
    assert role_update.status_code == 409
    assert (
        role_update.json()["detail"]
        == "Role is managed by Acme SSO and synchronized during SSO sign-in"
    )

    email_update = client.patch(
        f"/users/{user.id}",
        json={"email": "different@example.com"},
        headers=auth_headers["admin"],
    )
    assert email_update.status_code == 409
    assert (
        email_update.json()["detail"]
        == "Email is managed by Acme SSO for this SSO-provisioned account"
    )

    csrf_token = client.cookies.get("threatlens_csrf")
    password_change = client.post(
        "/auth/change-password",
        json={"current_password": "unused", "new_password": "RecoveryPass123!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert password_change.status_code == 403
    assert password_change.json()["detail"] == (
        "Password is managed by the identity provider for this SSO-provisioned account"
    )

    unlink = client.request(
        "DELETE",
        "/auth/oidc/account",
        json={"current_password": "unused"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert unlink.status_code == 400
    assert (
        unlink.json()["detail"]
        == "SSO-provisioned accounts cannot unlink their managed sign-in identity"
    )
    assert (
        db_session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.subject == "recovery-subject"
            )
        )
        is not None
    )


def test_user_directory_describes_sso_account_management_boundaries(
    client,
    auth_headers,
    db_session,
    monkeypatch,
):
    provider = _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "directory-subject",
            "email": "directory@example.com",
            "email_verified": True,
            "groups": [],
        },
    )
    callback = _start_and_complete(client)
    assert callback.headers["location"] == "http://testserver/start"

    response = client.get("/users", headers=auth_headers["admin"])

    assert response.status_code == 200
    entry = next(
        user for user in response.json() if user["email"] == "directory@example.com"
    )
    assert entry["provisioning_source"] == "oidc"
    assert entry["authentication_methods"] == ["oidc"]
    assert entry["oidc_provider_name"] == provider.name
    assert entry["oidc_linked_at"] is not None
    assert entry["oidc_last_login_at"] is not None
    assert entry["password_managed_by"] == "oidc"
    assert entry["role_managed_by"] == "oidc"

    provider_search = client.get(
        "/users/directory",
        params={"q": provider.name.lower()},
        headers=auth_headers["admin"],
    )
    assert provider_search.status_code == 200
    assert any(
        row["email"] == "directory@example.com"
        for row in provider_search.json()["users"]
    )
    sso_label_search = client.get(
        "/users/directory",
        params={"q": "sso-provisioned"},
        headers=auth_headers["admin"],
    )
    assert sso_label_search.status_code == 200
    assert any(
        row["email"] == "directory@example.com"
        for row in sso_label_search.json()["users"]
    )


def test_oidc_reauthentication_upgrades_and_rotates_exact_session_once(
    client, db_session, seed_users, monkeypatch
):
    user = seed_users["analyst"]
    _provider, original_token = _linked_oidc_session(db_session, user)
    sibling = create_auth_session(
        db_session,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.77",
        user_agent="Unrelated sibling session",
    )
    original_generation = user.auth_token_version
    db_session.commit()
    _mock_oidc_flow(
        monkeypatch,
        {
            "sub": "reauth-subject",
            "email": user.email,
            "email_verified": True,
            "amr": ["pwd", "mfa"],
            "acr": "urn:company:loa:2",
        },
    )
    authorization_parameters: dict[str, object] = {}

    def _authorization_url(_provider, _metadata, *, state, **kwargs):
        authorization_parameters.update(kwargs)
        return f"https://idp.example.com/authorize?state={state}"

    monkeypatch.setattr(
        "app.api.routes.oidc.build_oidc_authorization_url", _authorization_url
    )
    client.cookies.set("threatlens_session", original_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "reauth-csrf", domain="testserver.local")
    start = client.post("/auth/oidc/reauth", headers={"X-CSRF-Token": "reauth-csrf"})
    assert start.status_code == 200, start.text
    assert authorization_parameters["prompt"] == "login"
    assert authorization_parameters["max_age_seconds"] == 0
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
    transaction_cookie = client.cookies.get("threatlens_oidc_transaction")
    assert transaction_cookie

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"].endswith("oidc_reauth=success")
    rotated_token = client.cookies.get("threatlens_session", domain="testserver.local")
    assert rotated_token and rotated_token != original_token

    db_session.expire_all()
    original_session = db_session.get(
        AuthSession, extract_auth_session_id(original_token)
    )
    rotated_session = db_session.get(
        AuthSession, extract_auth_session_id(rotated_token)
    )
    assert original_session is not None and original_session.revoked_at is not None
    assert rotated_session is not None
    assert rotated_session.mfa_method == "external"
    assert rotated_session.identity_acr == "urn:company:loa:2"
    assert rotated_session.identity_amr_json == ["pwd", "mfa"]
    assert rotated_session.identity_authenticated_at is not None
    db_session.refresh(sibling.session)
    db_session.refresh(user)
    assert sibling.session.revoked_at is None
    assert user.auth_token_version == original_generation

    client.cookies.set("threatlens_session", original_token, domain="testserver.local")
    client.cookies.set(
        "threatlens_oidc_transaction",
        transaction_cookie,
        domain="testserver.local",
    )
    replay = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert replay.headers["location"].endswith("oidc_reauth=reauth_session_expired")


def test_oidc_reauthentication_rejects_provider_revision_race(
    client, db_session, seed_users, monkeypatch
):
    user = seed_users["analyst"]
    provider, session_token = _linked_oidc_session(db_session, user)
    _mock_oidc_flow(monkeypatch, {"sub": "reauth-subject", "amr": ["mfa"]})
    client.cookies.set("threatlens_session", session_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "reauth-csrf", domain="testserver.local")
    start = client.post("/auth/oidc/reauth", headers={"X-CSRF-Token": "reauth-csrf"})
    assert start.status_code == 200
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]

    db_session.execute(
        update(OIDCProvider)
        .where(OIDCProvider.id == provider.id)
        .values(config_revision=OIDCProvider.config_revision + 1)
    )
    db_session.commit()
    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"].endswith(
        "oidc_reauth=provider_configuration_changed"
    )
    assert (
        client.cookies.get("threatlens_session", domain="testserver.local")
        == session_token
    )


def test_oidc_reauthentication_rejects_session_generation_race(
    client, db_session, seed_users, monkeypatch
):
    user = seed_users["analyst"]
    _provider, session_token = _linked_oidc_session(db_session, user)
    _mock_oidc_flow(monkeypatch, {"sub": "reauth-subject", "amr": ["mfa"]})
    client.cookies.set("threatlens_session", session_token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "reauth-csrf", domain="testserver.local")
    start = client.post("/auth/oidc/reauth", headers={"X-CSRF-Token": "reauth-csrf"})
    assert start.status_code == 200
    state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]

    user.auth_token_version += 1
    db_session.commit()
    callback = client.get(
        "/auth/oidc/callback",
        params={"state": state, "code": "authorization-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"].endswith("oidc_reauth=reauth_session_expired")


def test_invalid_public_oidc_callback_does_not_create_durable_audit(client, db_session):
    before = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "auth.oidc.callback")
        .count()
    )

    response = client.get(
        "/auth/oidc/callback",
        params={"state": "invalid", "code": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    db_session.expire_all()
    after = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "auth.oidc.callback")
        .count()
    )
    assert after == before
