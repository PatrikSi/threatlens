from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.user import User
from app.services.oidc_client import OIDCClaims, OIDCMetadata
from app.services.secret_storage import is_encrypted_text


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
        "sync_roles_on_login": True,
    }
    payload.update(overrides)
    return payload


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
        "sync_roles_on_login": True,
    }
    values.update(overrides)
    provider = OIDCProvider(**values)
    db_session.add(provider)
    db_session.commit()
    return provider


def _mock_oidc_flow(monkeypatch, claims: dict):
    metadata = OIDCMetadata(
        issuer="https://idp.example.com",
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint="https://idp.example.com/userinfo",
        token_endpoint_auth_methods_supported=("none",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    monkeypatch.setattr("app.api.routes.oidc.load_oidc_metadata", lambda _provider: metadata)
    monkeypatch.setattr(
        "app.api.routes.oidc.build_oidc_authorization_url",
        lambda _provider, _metadata, *, state, nonce, code_verifier: f"https://idp.example.com/authorize?state={state}",
    )
    monkeypatch.setattr(
        "app.api.routes.oidc.exchange_oidc_code",
        lambda _provider, _metadata, *, code, code_verifier: {"id_token": "id-token", "access_token": "access-token"},
    )
    monkeypatch.setattr(
        "app.api.routes.oidc.validate_oidc_token_claims",
        lambda _provider, _metadata, _token, *, nonce: OIDCClaims(
            issuer="https://idp.example.com",
            subject=str(claims.get("sub", "subject-1")),
            claims=claims,
        ),
    )


def _start_and_complete(client: TestClient, *, start_path: str = "/auth/oidc/login"):
    if start_path == "/auth/oidc/link":
        csrf_token = client.cookies.get("threatlens_csrf")
        assert csrf_token
        start = client.post(start_path, headers={"X-CSRF-Token": csrf_token})
        assert start.status_code == 200
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


def test_admin_can_configure_oidc_without_secret_disclosure(client, auth_headers, db_session):
    response = client.put("/auth/oidc/provider", json=_provider_payload(), headers=auth_headers["admin"])

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["has_client_secret"] is True
    assert "client_secret" not in body
    assert body["callback_url"] == "https://threatlens.example.com/api/v1/auth/oidc/callback"
    provider = db_session.scalar(select(OIDCProvider))
    assert provider is not None
    assert is_encrypted_text(provider.client_secret_encrypted)
    assert "provider-secret" not in provider.client_secret_encrypted

    public = client.get("/auth/oidc/settings")
    assert public.status_code == 200
    assert public.json() == {"enabled": True, "provider_name": "Acme SSO"}
    assert client.get("/auth/oidc/provider", headers=auth_headers["viewer"]).status_code == 403


def test_oidc_provider_identity_key_cannot_change_after_link(client, auth_headers, db_session, seed_users):
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
        headers=auth_headers["admin"],
    )

    assert response.status_code == 409
    assert "cannot change" in response.json()["detail"]


def test_oidc_jit_login_provisions_verified_user_and_maps_role(client, db_session, monkeypatch):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "subject-1", "email": "new-user@example.com", "email_verified": True, "groups": ["soc"]},
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://testserver/"
    user = db_session.scalar(select(User).where(User.email == "new-user@example.com"))
    assert user is not None
    assert user.role == "analyst"
    assert user.password_login_enabled is False
    assert user.is_approved is True
    identity = db_session.scalar(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id))
    assert identity is not None
    assert identity.subject == "subject-1"
    assert client.get("/auth/me").status_code == 200


def test_oidc_jit_pending_user_is_created_without_a_session(client, db_session, monkeypatch):
    _configured_provider(db_session, auto_approve_users=False)
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "pending-subject", "email": "pending@example.com", "email_verified": True, "groups": []},
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == ["approval_required"]
    user = db_session.scalar(select(User).where(User.email == "pending@example.com"))
    assert user is not None
    assert user.is_approved is False
    assert user.password_login_enabled is False
    assert db_session.scalar(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)) is not None
    assert client.get("/auth/me").status_code == 401


def test_oidc_jit_rejects_unverified_email_without_creating_user(client, db_session, monkeypatch):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "subject-1", "email": "new-user@example.com", "email_verified": False, "groups": []},
    )

    callback = _start_and_complete(client)

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == ["verified_email_required"]
    assert db_session.scalar(select(User).where(User.email == "new-user@example.com")) is None


def test_oidc_jit_requires_explicit_link_for_existing_email(client, db_session, seed_users, monkeypatch):
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

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == ["email_link_required"]
    assert db_session.scalar(select(ExternalIdentity).where(ExternalIdentity.subject == "subject-1")) is None


def test_oidc_link_flow_binds_identity_to_initiating_browser_session(client, db_session, seed_users, monkeypatch):
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

    callback = _start_and_complete(client, start_path="/auth/oidc/link")

    assert callback.status_code == 302
    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_link"] == ["success"]
    identity = db_session.scalar(select(ExternalIdentity).where(ExternalIdentity.provider_id == provider.id))
    assert identity is not None
    assert identity.user_id == seed_users["analyst"].id


def test_oidc_link_start_requires_csrf_and_cookie_session(client, auth_headers, db_session, seed_users, monkeypatch):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "linked-subject"})

    bearer_response = client.post("/auth/oidc/link", headers=auth_headers["analyst"])
    assert bearer_response.status_code == 400
    assert bearer_response.json()["detail"] == "OIDC account linking requires a browser session"

    login = client.post(
        "/auth/login",
        json={"email": seed_users["analyst"].email, "password": "AnalystPass123!"},
    )
    assert login.status_code == 200
    missing_csrf = client.post("/auth/oidc/link")
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"] == "Missing or invalid CSRF token"


def test_oidc_callback_rejects_state_mismatch_and_clears_transaction_cookie(client, db_session, monkeypatch):
    _configured_provider(db_session)
    _mock_oidc_flow(monkeypatch, {"sub": "subject-1"})
    start = client.get("/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 302

    callback = client.get(
        "/auth/oidc/callback",
        params={"state": "wrong-state", "code": "authorization-code"},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(callback.headers["location"]).query)["oidc_error"] == ["invalid_state"]
    assert "threatlens_oidc_transaction" not in client.cookies
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.oidc.callback"))
    assert audit is not None
    assert audit.success is False


def test_oidc_role_sync_revokes_tokens_but_preserves_last_admin(client, db_session, seed_users, monkeypatch):
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
    db_session.refresh(admin)
    assert admin.role == "admin"
    skipped_audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "oidc.role.sync", AuditLog.success.is_(False))
    )
    assert skipped_audit is not None
    assert skipped_audit.metadata_json["reason"] == "last_active_admin"
    assert db_session.scalar(select(ApiToken).where(ApiToken.user_id == admin.id, ApiToken.revoked_at.is_not(None))) is None


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
        select(ApiToken).where(ApiToken.user_id == analyst.id, ApiToken.revoked_at.is_not(None))
    ).all()
    assert len(revoked_tokens) == 1
    assert client.get("/auth/me", headers=auth_headers["analyst"]).status_code == 401
    sync_audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "oidc.role.sync", AuditLog.success.is_(True))
    )
    assert sync_audit is not None
    assert sync_audit.metadata_json["previous_role"] == "analyst"
    assert sync_audit.metadata_json["role"] == "viewer"
    assert sync_audit.metadata_json["revoked_api_tokens"] == 1


def test_oidc_only_account_cannot_use_local_login_or_unlink(client, db_session, monkeypatch):
    _configured_provider(db_session)
    _mock_oidc_flow(
        monkeypatch,
        {"sub": "external-only", "email": "external-only@example.com", "email_verified": True, "groups": []},
    )
    callback = _start_and_complete(client)
    assert callback.headers["location"] == "http://testserver/"

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
    assert "local password" in unlink.json()["detail"]
    identity = db_session.scalar(select(ExternalIdentity).where(ExternalIdentity.subject == "external-only"))
    assert identity is not None
