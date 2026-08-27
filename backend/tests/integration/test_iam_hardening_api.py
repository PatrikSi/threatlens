from __future__ import annotations

import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pyotp
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.investigation import Investigation, InvestigationActivity
from app.models.mfa import (
    MFALoginChallenge,
    UserRecoveryCode,
    UserTOTPCredential,
)
from app.models.oidc import OIDCProvider
from app.models.user import PROVISIONING_SOURCE_LOCAL, PROVISIONING_SOURCE_OIDC, User
from app.services.investigations import (
    InvestigationActorNotEligibleError,
    create_investigation,
)
from app.services.auth_sessions import (
    create_auth_session,
    lock_user_auth_state,
    lock_user_auth_states,
    rotate_user_auth_sessions,
)
from app.services import auth_rate_limit
from app.services.local_mfa import (
    MFAChallengeError,
    confirm_totp_enrollment,
    consume_mfa_challenge,
    create_mfa_challenge,
    cleanup_pending_totp_enrollments,
    start_totp_enrollment,
    verify_active_mfa_code,
    verify_active_totp_code,
)
from app.services.secret_storage import decrypt_text
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.user_access import (
    LocalBreakGlassAdminRequiredError,
    acquire_active_admin_invariant_lock,
    acquire_oidc_provider_config_lock,
    acquire_oidc_provider_config_read_lock,
    ensure_active_approved_admin_remains,
    ensure_local_break_glass_admin_remains_when_oidc_disabled,
    ensure_viable_local_break_glass_admin_exists,
    load_user_for_access_update,
    lock_users_for_security_change,
)


@pytest.fixture(autouse=True)
def _reset_sensitive_mfa_throttle_state():
    for email in ("admin@example.com", "analyst@example.com", "viewer@example.com"):
        auth_rate_limit._emergency_clear_mfa_action_failures(email, "testclient")
    yield
    for email in ("admin@example.com", "analyst@example.com", "viewer@example.com"):
        auth_rate_limit._emergency_clear_mfa_action_failures(email, "testclient")


def _create_assigned_investigation(
    client: TestClient,
    *,
    headers: dict[str, str],
    assignee_user_id: uuid.UUID,
) -> dict:
    response = client.post(
        "/investigations",
        headers=headers,
        json={
            "title": "Identity lifecycle ownership guard",
            "description": "Ensures account changes cannot orphan response work.",
            "visibility": "private",
            "assignee_user_id": str(assignee_user_id),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str, password: str) -> dict:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _enable_mfa_via_api(client: TestClient, password: str) -> tuple[list[str], str]:
    login = _login(client, "admin@example.com", password)
    csrf = login["csrf_token"]
    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": password},
        headers={"x-csrf-token": csrf},
    )
    assert enrollment.status_code == 200, enrollment.text
    secret = enrollment.json()["secret"]
    confirmation = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"x-csrf-token": csrf},
    )
    assert confirmation.status_code == 200, confirmation.text
    rotated_csrf = client.cookies.get("threatlens_csrf")
    assert rotated_csrf and rotated_csrf != csrf
    return confirmation.json()["recovery_codes"], rotated_csrf


def _start_direct_totp_enrollment(db: Session, user: User):
    created_session = create_auth_session(
        db,
        user_id=user.id,
        auth_token_version=int(user.auth_token_version or 0),
        auth_method="local",
        mfa_method=None,
        client_ip="testclient",
        user_agent="pytest direct enrollment",
    )
    enrollment = start_totp_enrollment(
        db,
        user=user,
        enrollment_session_id=created_session.session.id,
        enrollment_auth_token_version=created_session.session.auth_token_version,
    )
    return enrollment, created_session.session


def _confirm_direct_totp_enrollment(
    db: Session,
    *,
    user: User,
    enrollment,
    session: AuthSession,
):
    return confirm_totp_enrollment(
        db,
        user_id=user.id,
        code=pyotp.TOTP(enrollment.secret).now(),
        enrollment_session_id=session.id,
        enrollment_auth_token_version=session.auth_token_version,
    )


def test_login_rejects_oversized_password_before_password_hashing(
    client: TestClient,
    monkeypatch,
):
    def _unexpected_password_hashing(*_args, **_kwargs):
        raise AssertionError("password hashing must not run")

    monkeypatch.setattr(
        "app.api.routes.auth.verify_password_and_update",
        _unexpected_password_hashing,
    )

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "x" * 257},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"


def test_new_login_uses_hashed_opaque_session_and_legacy_jwt_remains_valid(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    payload = _login(client, admin.email, "AdminPass123!")
    token = client.cookies.get("threatlens_session")

    assert payload["csrf_token"]
    assert token is not None and token.startswith("tls_")
    session = db_session.scalar(
        select(AuthSession).where(AuthSession.user_id == admin.id)
    )
    assert session is not None
    assert session.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in session.token_hash
    assert client.get("/auth/me").status_code == 200

    legacy_token = create_access_token(
        str(admin.id), token_version=admin.auth_token_version
    )
    client.cookies.set("threatlens_session", legacy_token)
    assert client.get("/auth/me").status_code == 200

    legacy_enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers={"x-csrf-token": client.cookies.get("threatlens_csrf") or ""},
    )
    assert legacy_enrollment.status_code == 403
    assert legacy_enrollment.json()["error"]["code"] == "opaque_session_required"


def test_pending_mfa_enrollment_is_bound_to_exact_initiating_session(
    client: TestClient, db_session, seed_users
):
    admin = seed_users["admin"]
    first_login = _login(client, admin.email, "AdminPass123!")
    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers={"x-csrf-token": first_login["csrf_token"]},
    )
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]

    second_login = _login(client, admin.email, "AdminPass123!")
    rejected = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"x-csrf-token": second_login["csrf_token"]},
    )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "mfa_enrollment_restart_required"
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(UserTOTPCredential).where(UserTOTPCredential.user_id == admin.id)
        )
        is None
    )


def test_account_access_reduction_requires_another_eligible_investigation_owner(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    analyst = seed_users["analyst"]
    admin = seed_users["admin"]
    investigation = _create_assigned_investigation(
        client,
        headers=auth_headers["analyst"],
        assignee_user_id=analyst.id,
    )

    blocked = client.patch(
        f"/users/{analyst.id}",
        headers=auth_headers["admin"],
        json={
            "role": "viewer",
            "expected_security_version": analyst.auth_token_version,
        },
    )

    assert blocked.status_code == 409
    assert (
        blocked.json()["error"]["code"] == "investigation_owner_reassignment_required"
    )
    assert "only eligible owner" in blocked.json()["detail"]
    db_session.refresh(analyst)
    assert analyst.role == "analyst"

    add_owner = client.post(
        f"/investigations/{investigation['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(admin.id),
            "role": "owner",
            "expected_version": investigation["version"],
        },
    )
    assert add_owner.status_code == 200, add_owner.text

    allowed = client.patch(
        f"/users/{analyst.id}",
        headers=auth_headers["admin"],
        json={
            "role": "viewer",
            "expected_security_version": analyst.auth_token_version,
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["role"] == "viewer"

    detail = client.get(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["admin"],
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["assignee_user_id"] is None
    assert detail.json()["version"] == add_owner.json()["version"] + 1
    activity = db_session.scalar(
        select(InvestigationActivity).where(
            InvestigationActivity.investigation_id == uuid.UUID(investigation["id"]),
            InvestigationActivity.action == "investigation.assignee.cleared",
        )
    )
    assert activity is not None
    assert activity.actor_user_id == admin.id


def test_user_access_updates_preserve_legacy_requests_and_fence_supplied_version(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    original_version = viewer.auth_token_version

    legacy = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={"role": "analyst"},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["role"] == "analyst"
    assert legacy.json()["security_version"] == original_version + 1
    compatibility_audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "users.compatibility.unversioned_security_update",
            AuditLog.resource_id == str(viewer.id),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert compatibility_audit is not None
    assert compatibility_audit.metadata_json["security_version_before_update"] == (
        original_version
    )

    first = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={
            "role": "viewer",
            "expected_security_version": original_version + 1,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["role"] == "viewer"
    assert first.json()["security_version"] == original_version + 2

    stale = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={
            "is_approved": False,
            "expected_security_version": original_version,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "user_security_version_conflict"
    assert stale.headers["x-current-security-version"] == str(original_version + 2)
    assert stale.json()["error"]["context"]["current_security_version"] == (
        original_version + 2
    )

    db_session.refresh(viewer)
    assert viewer.role == "viewer"
    assert viewer.is_approved is True


def test_user_password_reset_preserves_legacy_request_and_fences_supplied_version(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    original_version = int(viewer.auth_token_version or 0)

    legacy = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={"password": "UpdatedViewerPass123!"},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["security_version"] == original_version + 1
    compatibility_audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "users.compatibility.unversioned_password_update",
            AuditLog.resource_id == str(viewer.id),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert compatibility_audit is not None
    assert compatibility_audit.metadata_json["security_version_before_update"] == (
        original_version
    )

    stale = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={
            "password": "SecondViewerPass123!",
            "expected_security_version": original_version,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "user_security_version_conflict"

    updated = client.patch(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
        json={
            "password": "SecondViewerPass123!",
            "expected_security_version": original_version + 1,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["security_version"] == original_version + 2
    db_session.refresh(viewer)
    assert verify_password("SecondViewerPass123!", viewer.password_hash)


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "viewer"},
        {"is_active": False},
        {"is_approved": False},
    ],
)
def test_oidc_disabled_preserves_local_break_glass_admin(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    payload,
):
    actor = seed_users["admin"]
    actor.password_login_enabled = False
    actor.provisioning_source = PROVISIONING_SOURCE_OIDC
    local_admin = User(
        email=f"local.break.glass.{uuid.uuid4()}@example.com",
        password_hash=get_password_hash("LocalAdminPass123!"),
        role="admin",
        is_active=True,
        is_approved=True,
        password_login_enabled=True,
        provisioning_source=PROVISIONING_SOURCE_LOCAL,
    )
    db_session.add_all(
        [
            local_admin,
            OIDCProvider(
                system_key="primary",
                name="Disabled SSO",
                enabled=False,
                issuer_url="https://idp.example.com",
                client_id="threatlens",
                client_auth_method="none",
                public_base_url="https://threatlens.example.com",
                scopes=["openid", "email"],
            ),
        ]
    )
    db_session.commit()

    response = client.patch(
        f"/users/{local_admin.id}",
        headers=auth_headers["admin"],
        json={
            **payload,
            "expected_security_version": local_admin.auth_token_version,
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "oidc_break_glass_admin_required"
    db_session.refresh(local_admin)
    assert local_admin.role == "admin"
    assert local_admin.is_active is True
    assert local_admin.is_approved is True
    rejected_audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "users.update",
            AuditLog.resource_id == str(local_admin.id),
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert rejected_audit is not None
    assert rejected_audit.metadata_json["reason"] == ("oidc_break_glass_admin_required")


def test_concurrent_oidc_disable_and_admin_demotion_preserve_break_glass(
    database_engine,
):
    local_admin_id = uuid.uuid4()
    sso_admin_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    with Session(bind=database_engine) as seed_session:
        seed_session.add_all(
            [
                User(
                    id=local_admin_id,
                    email=f"concurrent.local.{uuid.uuid4()}@example.com",
                    password_hash=get_password_hash("LocalAdminPass123!"),
                    role="admin",
                    is_active=True,
                    is_approved=True,
                    password_login_enabled=True,
                ),
                User(
                    id=sso_admin_id,
                    email=f"concurrent.sso.{uuid.uuid4()}@example.com",
                    password_hash=get_password_hash("UnusedAdminPass123!"),
                    role="admin",
                    is_active=True,
                    is_approved=True,
                    password_login_enabled=False,
                    provisioning_source=PROVISIONING_SOURCE_OIDC,
                ),
                OIDCProvider(
                    id=provider_id,
                    system_key="primary",
                    name="Concurrent SSO",
                    enabled=True,
                    issuer_url="https://idp.example.com",
                    client_id="threatlens",
                    client_auth_method="none",
                    public_base_url="https://threatlens.example.com",
                    scopes=["openid", "email"],
                ),
            ]
        )
        seed_session.commit()

    started = Event()

    def _demote_local_admin() -> str:
        with Session(bind=database_engine) as contender_session:
            started.set()
            try:
                locked = lock_users_for_security_change(
                    contender_session, [local_admin_id]
                )[local_admin_id]
                ensure_active_approved_admin_remains(
                    contender_session,
                    locked,
                    next_role="viewer",
                    next_is_active=True,
                    next_is_approved=True,
                )
                ensure_local_break_glass_admin_remains_when_oidc_disabled(
                    contender_session,
                    locked,
                    next_role="viewer",
                    next_is_active=True,
                    next_is_approved=True,
                    next_password_login_enabled=True,
                )
                locked.role = "viewer"
                contender_session.commit()
                return "committed"
            except LocalBreakGlassAdminRequiredError:
                contender_session.rollback()
                return "rejected"

    provider_session = Session(bind=database_engine)
    try:
        acquire_active_admin_invariant_lock(provider_session)
        acquire_oidc_provider_config_lock(provider_session)
        provider = provider_session.scalar(
            select(OIDCProvider).where(OIDCProvider.id == provider_id).with_for_update()
        )
        assert provider is not None
        ensure_viable_local_break_glass_admin_exists(provider_session)
        provider.enabled = False
        provider_session.flush()

        with ThreadPoolExecutor(max_workers=1) as executor:
            contender = executor.submit(_demote_local_admin)
            assert started.wait(timeout=2)
            time.sleep(0.1)
            assert not contender.done()
            provider_session.commit()
            assert contender.result(timeout=5) == "rejected"
    finally:
        provider_session.rollback()
        provider_session.close()

    with Session(bind=database_engine) as verify_session:
        local_admin = verify_session.get(User, local_admin_id)
        provider = verify_session.get(OIDCProvider, provider_id)
        assert local_admin is not None and local_admin.role == "admin"
        assert provider is not None and provider.enabled is False
        verify_session.execute(
            delete(OIDCProvider).where(OIDCProvider.id == provider_id)
        )
        verify_session.execute(
            delete(User).where(User.id.in_([local_admin_id, sso_admin_id]))
        )
        verify_session.commit()


def test_admin_can_load_one_user_outside_the_directory_page(
    client: TestClient,
    auth_headers,
    seed_users,
):
    viewer = seed_users["viewer"]

    response = client.get(
        f"/users/{viewer.id}",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(viewer.id)
    assert response.json()["email"] == viewer.email

    forbidden = client.get(
        f"/users/{viewer.id}",
        headers=auth_headers["viewer"],
    )
    assert forbidden.status_code == 403

    missing = client.get(
        f"/users/{uuid.uuid4()}",
        headers=auth_headers["admin"],
    )
    assert missing.status_code == 404


def test_user_directory_search_matches_role_status_and_account_labels(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    seed_users["viewer"].is_active = False
    seed_users["analyst"].is_approved = False
    db_session.commit()

    expected = {
        "inactive": {"viewer@example.com"},
        "pending": {"analyst@example.com"},
        "admin": {"admin@example.com"},
        "local": {
            "admin@example.com",
            "analyst@example.com",
            "viewer@example.com",
        },
    }
    for query, expected_emails in expected.items():
        response = client.get(
            "/users/directory",
            params={"q": query},
            headers=auth_headers["admin"],
        )
        assert response.status_code == 200, response.text
        assert {row["email"] for row in response.json()["users"]} == expected_emails


def test_investigation_creation_rechecks_owner_after_concurrent_role_reduction(
    database_engine,
):
    analyst_id = uuid.uuid4()
    candidate_loaded = Event()
    title = f"concurrent-owner-{uuid.uuid4()}"

    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=analyst_id,
                email=f"{title}@example.com",
                password_hash="not-used-by-this-test",
                role="analyst",
                is_active=True,
                is_approved=True,
                approved_at=datetime.now(timezone.utc),
            )
        )
        setup_db.commit()

    def _try_create() -> str:
        with Session(database_engine) as candidate_db:
            stale_user = candidate_db.get(User, analyst_id)
            assert stale_user is not None and stale_user.role == "analyst"
            candidate_loaded.set()
            try:
                create_investigation(
                    candidate_db,
                    user=stale_user,
                    title=title,
                    description="Must not be owned by a demoted account.",
                    severity="medium",
                    visibility="private",
                    assignee_user_id=analyst_id,
                )
            except InvestigationActorNotEligibleError as exc:
                candidate_db.rollback()
                return str(exc)
            candidate_db.commit()
            return "created"

    try:
        with Session(database_engine) as access_db:
            locked_user = load_user_for_access_update(access_db, analyst_id)
            assert locked_user is not None
            locked_user.role = "viewer"
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(_try_create)
                assert candidate_loaded.wait(timeout=2)
                assert not result.done()
                access_db.commit()
                outcome = result.result(timeout=3)

        assert "analyst or administrator" in outcome
        with Session(database_engine) as verification_db:
            assert (
                verification_db.scalar(
                    select(Investigation.id).where(Investigation.title == title)
                )
                is None
            )
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == analyst_id))
            cleanup_db.commit()


def test_local_mfa_login_accepts_each_recovery_code_only_once(
    client: TestClient,
    db_session,
    seed_users,
):
    _ = seed_users
    recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    logout = client.post("/auth/logout", headers={"x-csrf-token": csrf})
    assert logout.status_code == 200

    challenge = _login(client, "admin@example.com", "AdminPass123!")
    assert challenge == {"token_type": "session_cookie", "mfa_required": True}
    assert client.cookies.get("threatlens_session") is None
    verified = client.post("/auth/mfa/verify", json={"code": recovery_codes[0]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["csrf_token"]
    assert client.get("/auth/me").status_code == 200

    second_csrf = verified.json()["csrf_token"]
    assert (
        client.post("/auth/logout", headers={"x-csrf-token": second_csrf}).status_code
        == 200
    )
    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True
    challenge_token = client.cookies.get("threatlens_mfa_challenge")
    assert challenge_token is not None
    challenge_id = uuid.UUID(hex=challenge_token.split("_", 2)[1])
    replay = client.post("/auth/mfa/verify", json={"code": recovery_codes[0]})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "mfa_code_invalid"
    assert "already been used" in replay.json()["detail"]

    challenge_row = db_session.get(MFALoginChallenge, challenge_id)
    assert challenge_row is not None and challenge_row.attempt_count == 1


def test_mfa_enable_rotates_current_cookie_and_invalidates_legacy_sessions(
    client: TestClient,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    old_opaque_token = client.cookies.get("threatlens_session")
    assert old_opaque_token
    legacy_token = create_access_token(
        str(admin.id),
        token_version=admin.auth_token_version,
    )

    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert enrollment.status_code == 200, enrollment.text
    confirmation = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(enrollment.json()["secret"]).now()},
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert confirmation.status_code == 200, confirmation.text
    rotated_token = client.cookies.get("threatlens_session")
    assert rotated_token and rotated_token != old_opaque_token

    client.cookies.set("threatlens_session", old_opaque_token)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", legacy_token)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", rotated_token)
    assert client.get("/auth/me").status_code == 200


def test_pending_mfa_enrollment_can_be_cancelled_and_expires_server_side(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    enrollment = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert enrollment.status_code == 200, enrollment.text

    cancelled = client.delete(
        "/auth/security/mfa/enrollment",
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert (
        db_session.scalar(
            select(UserTOTPCredential).where(UserTOTPCredential.user_id == admin.id)
        )
        is None
    )

    replacement = client.post(
        "/auth/security/mfa/enroll",
        json={"current_password": "AdminPass123!"},
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert replacement.status_code == 200, replacement.text
    credential = db_session.scalar(
        select(UserTOTPCredential).where(UserTOTPCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    expired = client.post(
        "/auth/security/mfa/confirm",
        json={"code": pyotp.TOTP(replacement.json()["secret"]).now()},
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert expired.status_code == 409
    assert "expired" in expired.json()["detail"].lower()


class _UnavailableAuthRedis:
    def __getattr__(self, _name):
        def unavailable(*_args, **_kwargs):
            raise auth_rate_limit.redis.RedisError("redis unavailable")

        return unavailable


def test_sensitive_mfa_actions_fail_closed_and_are_audited_when_redis_is_down(
    client: TestClient,
    db_session,
    seed_users,
    monkeypatch,
):
    _ = seed_users
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    auth_rate_limit._emergency_versions.clear()
    auth_rate_limit._emergency_clear_mfa_action_failures(
        "admin@example.com", "testclient"
    )
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableAuthRedis())
    first = client.post(
        "/auth/security/mfa/recovery-codes",
        json={"current_password": "AdminPass123!", "code": "invalid-code"},
        headers={"x-csrf-token": csrf},
    )
    second = client.post(
        "/auth/security/mfa/recovery-codes",
        json={"current_password": "AdminPass123!", "code": "invalid-code"},
        headers={"x-csrf-token": csrf},
    )

    assert first.status_code == 503
    assert second.status_code == 503
    assert "No MFA code was checked" in first.json()["detail"]
    assert first.headers.get("retry-after") == "5"
    assert second.headers.get("retry-after")
    failure_audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "auth.mfa.recovery_codes.regenerate",
            AuditLog.success.is_(False),
        )
    ).all()
    assert len(failure_audits) == 2
    assert {entry.metadata_json["reason"] for entry in failure_audits} == {
        "throttle_unavailable"
    }
    auth_rate_limit._emergency_clear_mfa_action_failures(
        "admin@example.com", "testclient"
    )


def test_recovery_code_cannot_replace_all_recovery_codes(
    client: TestClient,
    seed_users,
):
    _ = seed_users
    recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")

    rejected = client.post(
        "/auth/security/mfa/recovery-codes",
        json={
            "current_password": "AdminPass123!",
            "code": recovery_codes[0],
        },
        headers={"x-csrf-token": csrf},
    )
    assert rejected.status_code == 400
    assert "cannot authorize replacement" in rejected.json()["detail"]

    assert (
        client.post(
            "/auth/logout",
            headers={"x-csrf-token": csrf},
        ).status_code
        == 200
    )
    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True
    verification = client.post(
        "/auth/mfa/verify",
        json={"code": recovery_codes[0]},
    )
    assert verification.status_code == 200, verification.text


def test_terminal_mfa_failure_clears_cookie_and_persists_challenge_state(
    client: TestClient,
    db_session,
    seed_users,
):
    _ = seed_users
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    assert (
        client.post("/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    )
    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True
    challenge_token = client.cookies.get("threatlens_mfa_challenge")
    assert challenge_token is not None
    challenge_id = uuid.UUID(hex=challenge_token.split("_", 2)[1])

    response = None
    for _attempt in range(get_settings().auth_mfa_challenge_max_attempts):
        response = client.post("/auth/mfa/verify", json={"code": "invalid-code"})

    assert response is not None and response.status_code == 401
    assert (
        response.json()["detail"]
        == "Too many failed MFA attempts. Start sign-in again."
    )
    assert client.cookies.get("threatlens_mfa_challenge") is None
    challenge = db_session.get(MFALoginChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is not None
    assert challenge.attempt_count == challenge.max_attempts


def test_mfa_failures_remain_in_login_throttle_across_new_challenges(
    client: TestClient,
    seed_users,
):
    _ = seed_users
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    assert (
        client.post("/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    )
    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True

    for _attempt in range(get_settings().auth_mfa_challenge_max_attempts):
        response = client.post("/auth/mfa/verify", json={"code": "invalid-code"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mfa_challenge_attempts_exhausted"

    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True
    response = None
    for _attempt in range(get_settings().auth_login_max_attempts):
        response = client.post("/auth/mfa/verify", json={"code": "invalid-code"})
        if response.status_code == 429:
            break

    assert response is not None and response.status_code == 429
    assert response.headers.get("retry-after")
    assert client.cookies.get("threatlens_mfa_challenge") is None
    assert (
        client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        ).status_code
        == 429
    )


def test_expired_mfa_challenge_is_consumed_and_cookie_is_cleared(
    client: TestClient,
    db_session,
    seed_users,
):
    _ = seed_users
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    assert (
        client.post("/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    )
    assert _login(client, "admin@example.com", "AdminPass123!")["mfa_required"] is True
    challenge_token = client.cookies.get("threatlens_mfa_challenge")
    assert challenge_token is not None
    challenge_id = uuid.UUID(hex=challenge_token.split("_", 2)[1])
    challenge = db_session.get(MFALoginChallenge, challenge_id)
    assert challenge is not None
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    response = client.post("/auth/mfa/verify", json={"code": "invalid-code"})

    assert response.status_code == 401
    assert client.cookies.get("threatlens_mfa_challenge") is None
    db_session.refresh(challenge)
    assert challenge.consumed_at is not None


def test_mfa_challenge_is_invalidated_when_account_security_generation_changes(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    assert (
        client.post("/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    )
    assert _login(client, admin.email, "AdminPass123!")["mfa_required"] is True

    admin.auth_token_version += 1
    db_session.commit()

    rejected = client.post("/auth/mfa/verify", json={"code": recovery_codes[0]})
    assert rejected.status_code == 401
    assert (
        rejected.json()["detail"]
        == "Account security changed after password verification. Start sign-in again."
    )
    assert rejected.json()["error"]["code"] == "mfa_challenge_security_changed"
    assert client.cookies.get("threatlens_mfa_challenge") is None

    assert _login(client, admin.email, "AdminPass123!")["mfa_required"] is True
    accepted = client.post("/auth/mfa/verify", json={"code": recovery_codes[0]})
    assert accepted.status_code == 200, accepted.text


def test_mfa_verification_serializes_with_account_security_changes(database_engine):
    user_id = uuid.uuid4()
    email = f"mfa-race-{user_id}@example.com"
    verification_started = Event()

    with Session(database_engine) as setup_db:
        user = User(
            id=user_id,
            email=email,
            password_hash="not-used-by-this-test",
            role="analyst",
            is_active=True,
            is_approved=True,
            approved_at=datetime.now(timezone.utc),
        )
        setup_db.add(user)
        setup_db.flush()
        enrollment, enrollment_session = _start_direct_totp_enrollment(setup_db, user)
        confirmation = _confirm_direct_totp_enrollment(
            setup_db,
            user=user,
            enrollment=enrollment,
            session=enrollment_session,
        )
        recovery_code = confirmation.recovery_codes[0]
        challenge = create_mfa_challenge(
            setup_db,
            user_id=user_id,
            auth_token_version=0,
            client_ip="192.0.2.20",
            user_agent="Concurrent verification",
        )
        challenge_token = challenge.token
        setup_db.commit()

    def _verify() -> str:
        with Session(database_engine) as verification_db:
            verification_started.set()
            try:
                consume_mfa_challenge(
                    verification_db,
                    token=challenge_token,
                    code=recovery_code,
                )
            except MFAChallengeError as exc:
                verification_db.commit()
                return str(exc)
            verification_db.commit()
            return "verified"

    try:
        with Session(database_engine) as access_db:
            locked_user = lock_user_auth_state(access_db, user_id)
            assert locked_user is not None
            locked_user.auth_token_version += 1
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(_verify)
                assert verification_started.wait(timeout=2)
                assert not result.done()
                access_db.commit()
                outcome = result.result(timeout=3)

        assert outcome.startswith("Account security changed")
        with Session(database_engine) as retry_db:
            user = retry_db.get(User, user_id)
            assert user is not None
            retry = create_mfa_challenge(
                retry_db,
                user_id=user_id,
                auth_token_version=user.auth_token_version,
                client_ip="192.0.2.21",
                user_agent="Retry verification",
            )
            _challenge, verification = consume_mfa_challenge(
                retry_db,
                token=retry.token,
                code=recovery_code,
            )
            retry_db.commit()
            assert verification.method == "recovery_code"
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()


def test_pending_mfa_cleanup_skips_a_concurrent_confirmation(database_engine):
    user_id = uuid.uuid4()
    confirmation_locked = Event()
    allow_confirmation_commit = Event()
    with Session(database_engine) as setup_db:
        user = User(
            id=user_id,
            email=f"cleanup-race-{user_id}@example.com",
            password_hash="not-used",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        setup_db.add(user)
        setup_db.flush()
        enrollment, enrollment_session = _start_direct_totp_enrollment(setup_db, user)
        setup_db.execute(
            update(UserTOTPCredential)
            .where(UserTOTPCredential.user_id == user_id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=2))
        )
        session_id = enrollment_session.id
        auth_generation = enrollment_session.auth_token_version
        code = pyotp.TOTP(enrollment.secret).now()
        setup_db.commit()

    def _confirm() -> None:
        with Session(database_engine) as confirm_db:
            locked_user = lock_user_auth_state(confirm_db, user_id)
            assert locked_user is not None
            confirm_totp_enrollment(
                confirm_db,
                user_id=user_id,
                code=code,
                enrollment_session_id=session_id,
                enrollment_auth_token_version=auth_generation,
            )
            confirmation_locked.set()
            assert allow_confirmation_commit.wait(timeout=3)
            confirm_db.commit()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(_confirm)
            assert confirmation_locked.wait(timeout=3)
            with Session(database_engine) as cleanup_db:
                deleted = cleanup_pending_totp_enrollments(
                    cleanup_db,
                    now=datetime.now(timezone.utc),
                    ttl_seconds=60,
                )
                cleanup_db.commit()
            assert deleted == 0
            allow_confirmation_commit.set()
            result.result(timeout=3)

        with Session(database_engine) as verify_db:
            credential = verify_db.scalar(
                select(UserTOTPCredential).where(UserTOTPCredential.user_id == user_id)
            )
            assert credential is not None and credential.status == "active"
    finally:
        allow_confirmation_commit.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()


def test_canonical_iam_lock_order_avoids_deadlock_for_both_uuid_orders(
    database_engine,
):
    lower_id = uuid.UUID(int=1_001)
    higher_id = uuid.UUID(int=2_002)
    with Session(database_engine) as setup_db:
        setup_db.add_all(
            [
                User(
                    id=user_id,
                    email=f"lock-order-{user_id}@example.com",
                    password_hash="not-used",
                    role="analyst",
                    is_active=True,
                    is_approved=True,
                )
                for user_id in (lower_id, higher_id)
            ]
        )
        setup_db.commit()

    start = Event()

    def _lock(order: list[uuid.UUID]) -> list[uuid.UUID]:
        with Session(database_engine) as lock_db:
            assert start.wait(timeout=3)
            locked = lock_users_for_security_change(lock_db, order)
            locked_ids = list(locked)
            time.sleep(0.1)
            lock_db.commit()
            return locked_ids

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(_lock, [lower_id, higher_id])
            second = executor.submit(_lock, [higher_id, lower_id])
            start.set()
            assert first.result(timeout=5) == [lower_id, higher_id]
            assert second.result(timeout=5) == [lower_id, higher_id]
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id.in_([lower_id, higher_id])))
            cleanup_db.commit()


def test_credential_user_lock_does_not_wait_for_active_admin_invariant_lock(
    database_engine,
):
    user_id = uuid.UUID(int=3_003)
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=user_id,
                email="credential-lock@example.com",
                password_hash="not-used",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup_db.commit()

    credential_lock_started = Event()

    def _lock_credential_user() -> uuid.UUID:
        with Session(database_engine) as credential_db:
            credential_lock_started.set()
            locked = lock_user_auth_states(credential_db, [user_id])
            credential_db.commit()
            return next(iter(locked))

    try:
        with Session(database_engine) as invariant_db:
            acquire_active_admin_invariant_lock(invariant_db)
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(_lock_credential_user)
                assert credential_lock_started.wait(timeout=2)
                assert result.result(timeout=2) == user_id
            invariant_db.rollback()
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()


def test_oidc_provider_update_and_callback_lock_orders_do_not_deadlock(
    database_engine,
):
    user_id = uuid.UUID(int=4_004)
    provider_id = uuid.UUID(int=5_005)
    with Session(database_engine) as setup_db:
        setup_db.add(
            User(
                id=user_id,
                email="oidc-lock-order@example.com",
                password_hash="not-used",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup_db.add(
            OIDCProvider(
                id=provider_id,
                system_key="iam-lock-order",
                name="IAM lock order",
                issuer_url="https://idp-lock-order.example.com",
                client_id="lock-order",
                client_auth_method="none",
                public_base_url="https://threatlens-lock-order.example.com",
                scopes=["openid"],
                enabled=True,
            )
        )
        setup_db.commit()

    start = Event()

    def _lock_callback_path() -> str:
        with Session(database_engine) as callback_db:
            assert start.wait(timeout=3)
            acquire_active_admin_invariant_lock(callback_db)
            acquire_oidc_provider_config_read_lock(callback_db)
            provider = callback_db.scalar(
                select(OIDCProvider)
                .where(OIDCProvider.id == provider_id)
                .with_for_update(read=True)
            )
            locked_user = lock_user_auth_states(callback_db, [user_id]).get(user_id)
            assert provider is not None and locked_user is not None
            time.sleep(0.1)
            callback_db.commit()
            return "callback"

    def _lock_provider_update_path() -> str:
        with Session(database_engine) as update_db:
            assert start.wait(timeout=3)
            acquire_oidc_provider_config_lock(update_db)
            provider = update_db.scalar(
                select(OIDCProvider)
                .where(OIDCProvider.id == provider_id)
                .with_for_update()
            )
            locked_user = lock_user_auth_states(update_db, [user_id]).get(user_id)
            assert provider is not None and locked_user is not None
            time.sleep(0.1)
            update_db.commit()
            return "update"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            callback = executor.submit(_lock_callback_path)
            update_provider = executor.submit(_lock_provider_update_path)
            start.set()
            assert {callback.result(timeout=5), update_provider.result(timeout=5)} == {
                "callback",
                "update",
            }
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(OIDCProvider).where(OIDCProvider.id == provider_id)
            )
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()


def test_concurrent_oidc_callback_provider_reads_do_not_serialize(
    database_engine,
):
    provider_id = uuid.UUID(int=5_105)
    with Session(database_engine) as setup_db:
        setup_db.add(
            OIDCProvider(
                id=provider_id,
                system_key="iam-shared-callback-lock",
                name="IAM shared callback lock",
                issuer_url="https://idp-shared-lock.example.com",
                client_id="shared-lock",
                client_auth_method="none",
                public_base_url="https://threatlens-shared-lock.example.com",
                scopes=["openid"],
                enabled=True,
            )
        )
        setup_db.commit()

    acquired = [Event(), Event()]
    release = Event()

    def _read_provider(index: int) -> int:
        with Session(database_engine) as callback_db:
            acquire_oidc_provider_config_read_lock(callback_db)
            provider = callback_db.scalar(
                select(OIDCProvider)
                .where(OIDCProvider.id == provider_id)
                .with_for_update(read=True)
            )
            assert provider is not None
            acquired[index].set()
            assert release.wait(timeout=3)
            callback_db.commit()
            return index

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            readers = [executor.submit(_read_provider, index) for index in range(2)]
            assert acquired[0].wait(timeout=2)
            assert acquired[1].wait(timeout=2)
            release.set()
            assert {reader.result(timeout=3) for reader in readers} == {0, 1}
    finally:
        release.set()
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(OIDCProvider).where(OIDCProvider.id == provider_id)
            )
            cleanup_db.commit()


def test_recovery_codes_survive_application_encryption_key_rotation(
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["admin"]
    previous_key = get_settings().app_data_encryption_key
    assert previous_key
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, user)
    confirmation = _confirm_direct_totp_enrollment(
        db_session,
        user=user,
        enrollment=enrollment,
        session=enrollment_session,
    )
    db_session.commit()
    stored_hash = db_session.scalar(
        select(UserRecoveryCode.code_hash)
        .join(
            UserTOTPCredential,
            UserTOTPCredential.id == UserRecoveryCode.credential_id,
        )
        .where(UserTOTPCredential.user_id == user.id)
    )
    assert stored_hash is not None and stored_hash.startswith("hmac:v1:")

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "rotated-mfa-key-" + "r" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", previous_key)
    get_settings.cache_clear()
    try:
        inventory = scan_encrypted_data_inventory(db_session)
        assert inventory.key_retirement_blocked is True
        assert inventory.mfa_recovery_code_hashes.previous_key_codes > 0
        verification = verify_active_mfa_code(
            db_session,
            user_id=user.id,
            code=confirmation.recovery_codes[0],
        )
    finally:
        get_settings.cache_clear()

    assert verification.method == "recovery_code"


def test_successful_totp_use_lazily_rotates_the_encrypted_secret(
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["admin"]
    previous_key = get_settings().app_data_encryption_key
    assert previous_key
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, user)
    confirmation = _confirm_direct_totp_enrollment(
        db_session,
        user=user,
        enrollment=enrollment,
        session=enrollment_session,
    )
    credential = confirmation.credential
    assert credential.last_accepted_step is not None
    previous_ciphertext = credential.secret_encrypted
    db_session.commit()

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "rotated-totp-key-" + "r" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", previous_key)
    get_settings.cache_clear()
    future = datetime.fromtimestamp(
        (credential.last_accepted_step + 2) * 30,
        tz=timezone.utc,
    )
    verification = verify_active_totp_code(
        db_session,
        user_id=user.id,
        code=pyotp.TOTP(enrollment.secret).at(future),
        now=future,
    )
    db_session.flush()

    assert verification.method == "totp"
    assert credential.secret_encrypted != previous_ciphertext
    rotated_ciphertext = credential.secret_encrypted

    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS")
    get_settings.cache_clear()
    assert decrypt_text(rotated_ciphertext) == enrollment.secret


def test_session_inventory_and_revoke_others_are_scoped_to_the_current_user(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    original_token = client.cookies.get("threatlens_session")
    assert original_token
    original = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash
            == hashlib.sha256(original_token.encode()).hexdigest()
        )
    )
    assert original is not None
    original_authenticated_at = original.authenticated_at
    original_absolute_expiry = original.absolute_expires_at
    other = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.8",
        user_agent="Other browser",
    )
    db_session.commit()

    inventory = client.get("/auth/security/sessions")
    assert inventory.status_code == 200
    assert inventory.headers["cache-control"] == "no-store"
    assert inventory.json()["active_count"] == 2
    assert sum(1 for row in inventory.json()["sessions"] if row["current"]) == 1

    revoked = client.post(
        "/auth/security/sessions/revoke-others",
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_count"] == 1
    rotated_token = client.cookies.get("threatlens_session")
    assert rotated_token and rotated_token != original_token
    rotated_session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hashlib.sha256(rotated_token.encode()).hexdigest()
        )
    )
    assert rotated_session is not None
    assert rotated_session.authenticated_at == original_authenticated_at
    assert rotated_session.absolute_expires_at == original_absolute_expiry
    db_session.refresh(other.session)
    assert other.session.revoked_at is not None
    client.cookies.set("threatlens_session", original_token)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", rotated_token)
    assert client.get("/auth/me").status_code == 200


def test_revoke_others_requires_recent_authentication_without_extending_session(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    token = client.cookies.get("threatlens_session")
    assert token
    current = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    assert current is not None
    current.authenticated_at = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().auth_recent_auth_seconds + 1
    )
    other = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=admin.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.9",
        user_agent="Other browser",
    )
    db_session.commit()

    rejected = client.post(
        "/auth/security/sessions/revoke-others",
        headers={"x-csrf-token": login["csrf_token"]},
    )

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "local_reauthentication_required"
    assert rejected.json()["error"]["context"]["reauthentication_endpoint"] == (
        "/auth/security/reauthenticate"
    )
    assert client.cookies.get("threatlens_session") == token
    db_session.refresh(other.session)
    assert other.session.revoked_at is None


def test_single_non_current_session_revoke_requires_recent_auth_and_preserves_siblings(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    original_token = client.cookies.get("threatlens_session")
    assert original_token
    current = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash
            == hashlib.sha256(original_token.encode()).hexdigest()
        )
    )
    assert current is not None
    other = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=admin.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.19",
        user_agent="Other browser",
    )
    sibling = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=admin.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.20",
        user_agent="Sibling browser",
    )
    original_auth_generation = admin.auth_token_version
    current.authenticated_at = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().auth_recent_auth_seconds + 1
    )
    db_session.commit()

    rejected = client.delete(
        f"/auth/security/sessions/{other.session.id}",
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert rejected.status_code == 403
    db_session.refresh(other.session)
    assert other.session.revoked_at is None

    current.authenticated_at = datetime.now(timezone.utc)
    db_session.commit()
    accepted = client.delete(
        f"/auth/security/sessions/{other.session.id}",
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["revoked"] is True
    assert accepted.json()["current_session_revoked"] is False
    assert accepted.json()["revoked_session_count"] == 1
    assert accepted.json()["other_sessions_revoked"] == 0
    assert accepted.json()["auth_generation_rotated"] is False
    assert client.cookies.get("threatlens_session") == original_token
    db_session.refresh(other.session)
    db_session.refresh(sibling.session)
    db_session.refresh(admin)
    assert other.session.revoked_at is not None
    assert sibling.session.revoked_at is None
    assert admin.auth_token_version == original_auth_generation
    assert client.get("/auth/me").status_code == 200

    current_revoked = client.delete(
        f"/auth/security/sessions/{current.id}",
        headers={"x-csrf-token": login["csrf_token"]},
    )
    assert current_revoked.status_code == 200, current_revoked.text
    assert current_revoked.json() == {
        "status": "ok",
        "revoked": True,
        "current_session_revoked": True,
        "revoked_session_count": 1,
        "other_sessions_revoked": 0,
        "auth_generation_rotated": False,
    }
    assert client.cookies.get("threatlens_session") is None
    db_session.refresh(current)
    db_session.refresh(sibling.session)
    db_session.refresh(admin)
    assert current.revoked_at is not None
    assert sibling.session.revoked_at is None
    assert admin.auth_token_version == original_auth_generation
    assert client.get("/auth/me").status_code == 401


def test_local_reauthentication_rotates_only_the_exact_session(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    login = _login(client, admin.email, "AdminPass123!")
    original_token = client.cookies.get("threatlens_session")
    assert original_token
    current = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash
            == hashlib.sha256(original_token.encode()).hexdigest()
        )
    )
    assert current is not None
    sibling = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_token_version=admin.auth_token_version,
        auth_method="local",
        mfa_method=None,
        client_ip="203.0.113.21",
        user_agent="Sibling browser",
    )
    current.authenticated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    original_generation = admin.auth_token_version
    db_session.commit()

    response = client.post(
        "/auth/security/reauthenticate",
        headers={"x-csrf-token": login["csrf_token"]},
        json={"current_password": "AdminPass123!"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["auth_method"] == "local"
    assert response.json()["verification_method"] == "password"
    assert response.json()["session_rotated"] is True
    rotated_token = client.cookies.get("threatlens_session")
    assert rotated_token and rotated_token != original_token
    db_session.expire_all()
    assert db_session.get(AuthSession, current.id).revoked_at is not None
    assert db_session.get(AuthSession, sibling.session.id).revoked_at is None
    assert db_session.get(User, admin.id).auth_token_version == original_generation
    client.cookies.set("threatlens_session", original_token)
    assert client.get("/auth/me").status_code == 401
    client.cookies.set("threatlens_session", rotated_token)
    assert client.get("/auth/me").status_code == 200


def test_local_reauthentication_requires_current_totp_when_mfa_is_enabled(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    credential = db_session.scalar(
        select(UserTOTPCredential).where(UserTOTPCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.last_accepted_step = None
    db_session.commit()

    missing = client.post(
        "/auth/security/reauthenticate",
        headers={"x-csrf-token": csrf},
        json={"current_password": "AdminPass123!"},
    )
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "mfa_verification_required"

    code = pyotp.TOTP(decrypt_text(credential.secret_encrypted)).now()
    accepted = client.post(
        "/auth/security/reauthenticate",
        headers={"x-csrf-token": csrf},
        json={"current_password": "AdminPass123!", "code": code},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["verification_method"] == "password_totp"
    assert accepted.json()["session_rotated"] is True
    current = client.get("/auth/me")
    assert current.status_code == 200
    assert current.json()["authentication"]["session_auth_method"] == "local"
    assert current.json()["authentication"]["mfa_method"] == "totp"


def test_concurrent_session_rotations_serialize_auth_generations(database_engine):
    user_id = uuid.uuid4()
    email = f"session-race-{user_id}@example.com"
    second_loaded = Event()

    with Session(database_engine) as setup_db:
        user = User(
            id=user_id,
            email=email,
            password_hash="not-used-by-this-test",
            role="analyst",
            is_active=True,
            is_approved=True,
            approved_at=datetime.now(timezone.utc),
        )
        setup_db.add(user)
        setup_db.flush()
        create_auth_session(
            setup_db,
            user_id=user_id,
            auth_token_version=0,
            auth_method="local",
            mfa_method=None,
            client_ip="198.51.100.10",
            user_agent="Initial browser",
        )
        setup_db.commit()

    def _second_rotation() -> uuid.UUID:
        with Session(database_engine) as second_db:
            stale_user = second_db.get(User, user_id)
            assert stale_user is not None
            second_loaded.set()
            rotated = rotate_user_auth_sessions(
                second_db,
                user=stale_user,
                current_session_id=None,
                reason="concurrent_rotation_two",
                default_auth_method="local",
                mfa_method=None,
                client_ip="198.51.100.12",
                user_agent="Second rotation",
            )
            second_db.commit()
            return rotated.created.session.id

    try:
        with Session(database_engine) as first_db:
            locked_user = lock_user_auth_state(first_db, user_id)
            assert locked_user is not None
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(_second_rotation)
                assert second_loaded.wait(timeout=2)
                assert not result.done()
                first_rotation = rotate_user_auth_sessions(
                    first_db,
                    user=locked_user,
                    current_session_id=None,
                    reason="concurrent_rotation_one",
                    default_auth_method="local",
                    mfa_method=None,
                    client_ip="198.51.100.11",
                    user_agent="First rotation",
                )
                first_session_id = first_rotation.created.session.id
                first_db.commit()
                second_session_id = result.result(timeout=3)

        with Session(database_engine) as verification_db:
            user = verification_db.get(User, user_id)
            assert user is not None and user.auth_token_version == 2
            active = verification_db.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user_id,
                    AuthSession.auth_token_version == user.auth_token_version,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()
            assert [session.id for session in active] == [second_session_id]
            first_session = verification_db.get(AuthSession, first_session_id)
            assert first_session is not None
            assert first_session.revoked_at is not None
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()


def test_security_settings_reject_api_tokens(
    client: TestClient,
    auth_headers,
):
    response = client.get("/auth/security/sessions", headers=auth_headers["admin"])
    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "This security operation requires an authenticated browser session."
    )

    password_change = client.post(
        "/auth/change-password",
        json={"current_password": "AdminPass123!", "new_password": "AdminPass456!"},
        headers=auth_headers["admin"],
    )
    assert password_change.status_code == 403


def test_sso_account_reports_provider_managed_mfa(
    client: TestClient,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    viewer.provisioning_source = PROVISIONING_SOURCE_OIDC
    viewer.password_login_enabled = False
    created = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_method="oidc",
        mfa_method=None,
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token)

    response = client.get("/auth/security/mfa")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "local_mfa_available": False,
        "managed_by": "identity_provider",
        "enabled": False,
        "confirmed_at": None,
        "recovery_codes_remaining": 0,
    }


def test_recent_oidc_mfa_browser_session_can_create_own_durable_api_token(
    client: TestClient,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    admin = seed_users["admin"]
    viewer.provisioning_source = PROVISIONING_SOURCE_OIDC
    viewer.password_login_enabled = False
    created = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_method="oidc",
        mfa_method="external",
        identity_amr=["pwd", "mfa"],
        identity_authenticated_at=datetime.now(timezone.utc),
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "csrf-test", domain="testserver.local")

    response = client.post(
        "/tokens",
        json={
            "name": "oidc-browser-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "user_id": str(admin.id),
        },
        headers={"x-csrf-token": "csrf-test"},
    )

    assert response.status_code == 201, response.text
    token_value = response.json()["token"]
    db_session.expire_all()
    token = db_session.scalar(
        select(ApiToken).where(
            ApiToken.user_id == viewer.id,
            ApiToken.token_prefix == response.json()["token_prefix"],
        )
    )
    assert token is not None
    assert (
        db_session.scalar(
            select(ApiToken).where(
                ApiToken.user_id == admin.id,
                ApiToken.token_prefix == response.json()["token_prefix"],
            )
        )
        is None
    )
    audit = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "tokens.create",
            AuditLog.resource_id == str(token.id),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["credential_verification"] == (
        "oidc_recent_authentication"
    )

    client.cookies.clear()
    access = client.get(
        "/feeds",
        headers={"Authorization": f"Bearer {token_value}"},
    )
    assert access.status_code == 200


def test_recent_oidc_browser_session_without_mfa_cannot_create_api_token(
    client: TestClient,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    viewer.provisioning_source = PROVISIONING_SOURCE_OIDC
    viewer.password_login_enabled = False
    created = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_method="oidc",
        mfa_method=None,
        identity_authenticated_at=datetime.now(timezone.utc),
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token)
    client.cookies.set("threatlens_csrf", "csrf-test")

    response = client.post(
        "/tokens",
        json={
            "name": "plain-oidc-browser-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
        },
        headers={"x-csrf-token": "csrf-test"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "oidc_mfa_assurance_required"
    assert (
        db_session.scalar(select(ApiToken).where(ApiToken.user_id == viewer.id)) is None
    )


def test_stale_oidc_browser_session_requires_provider_reauthentication_for_token(
    client: TestClient,
    db_session,
    seed_users,
):
    viewer = seed_users["viewer"]
    viewer.provisioning_source = PROVISIONING_SOURCE_OIDC
    viewer.password_login_enabled = False
    stale_authentication = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().auth_recent_auth_seconds + 1
    )
    created = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_method="oidc",
        mfa_method=None,
        identity_authenticated_at=stale_authentication,
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", created.token, domain="testserver.local")
    client.cookies.set("threatlens_csrf", "csrf-test", domain="testserver.local")

    response = client.post(
        "/tokens",
        json={
            "name": "stale-oidc-browser-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
        },
        headers={"x-csrf-token": "csrf-test"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "oidc_reauthentication_required"
    assert response.json()["error"]["context"] == {
        "action": "api_token_create",
        "reauthentication_method": "oidc",
        "reauthentication_endpoint": "/auth/oidc/reauth",
    }
    assert (
        db_session.scalar(select(ApiToken).where(ApiToken.user_id == viewer.id)) is None
    )


def test_local_mfa_browser_token_creation_still_requires_password_and_factor(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")

    missing_factor = client.post(
        "/tokens",
        json={
            "name": "local-mfa-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "AdminPass123!",
        },
        headers={"x-csrf-token": csrf},
    )
    assert missing_factor.status_code == 403
    assert missing_factor.json()["error"]["code"] == "mfa_verification_required"

    created = client.post(
        "/tokens",
        json={
            "name": "local-mfa-token",
            "expires_in_days": 30,
            "scopes": ["read:feeds"],
            "current_password": "AdminPass123!",
            "code": recovery_codes[0],
        },
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 201, created.text

    db_session.expire_all()
    token = db_session.scalar(
        select(ApiToken).where(
            ApiToken.user_id == admin.id,
            ApiToken.token_prefix == created.json()["token_prefix"],
        )
    )
    assert token is not None
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "tokens.create",
            AuditLog.resource_id == str(token.id),
        )
    )
    assert audit is not None
    assert audit.metadata_json["credential_verification"] == (
        "local_password_recovery_code"
    )


def test_admin_mfa_reset_requires_browser_reauthentication_and_revokes_target_credentials(
    client: TestClient,
    db_session,
    seed_users,
    auth_headers,
):
    viewer = seed_users["viewer"]
    admin_recovery_codes, admin_csrf = _enable_mfa_via_api(client, "AdminPass123!")
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, viewer)
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=enrollment,
        session=enrollment_session,
    )
    target_session = create_auth_session(
        db_session,
        user_id=viewer.id,
        auth_method="local",
        mfa_method="totp",
        client_ip="203.0.113.9",
        user_agent="Target browser",
    )
    db_session.commit()

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={
            "reason": "User lost the enrolled device",
            "current_password": "AdminPass123!",
            "code": admin_recovery_codes[0],
        },
        headers={"x-csrf-token": admin_csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["disabled"] is True
    assert response.json()["revoked_auth_sessions"] >= 1
    assert response.json()["revoked_api_tokens"] >= 1
    assert (
        db_session.scalar(
            select(UserTOTPCredential).where(UserTOTPCredential.user_id == viewer.id)
        )
        is None
    )
    db_session.refresh(target_session.session)
    assert target_session.session.revoked_at is not None
    assert (
        db_session.scalar(
            select(ApiToken).where(
                ApiToken.user_id == viewer.id, ApiToken.revoked_at.is_(None)
            )
        )
        is None
    )
    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "users.mfa.reset")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["reason"] == "User lost the enrolled device"


def test_admin_mfa_reset_rejects_a_whitespace_only_audit_reason(
    client: TestClient,
    seed_users,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    login = _login(client, admin.email, "AdminPass123!")

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={"reason": "   ", "current_password": "AdminPass123!"},
        headers={"x-csrf-token": login["csrf_token"]},
    )

    assert response.status_code == 422
    assert "non-whitespace" in response.json()["detail"][0]["msg"]


def test_local_admin_without_mfa_cannot_reset_another_users_mfa(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, viewer)
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=enrollment,
        session=enrollment_session,
    )
    db_session.commit()
    login = _login(client, admin.email, "AdminPass123!")

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={
            "reason": "Recovery without administrator MFA",
            "current_password": "AdminPass123!",
        },
        headers={"x-csrf-token": login["csrf_token"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_mfa_assurance_required"


def test_admin_mfa_reset_fails_closed_when_shared_factor_throttle_is_unavailable(
    client: TestClient,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    _recovery_codes, csrf = _enable_mfa_via_api(client, "AdminPass123!")
    target_enrollment, target_enrollment_session = _start_direct_totp_enrollment(
        db_session, viewer
    )
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=target_enrollment,
        session=target_enrollment_session,
    )
    db_session.commit()
    monkeypatch.setattr(auth_rate_limit, "redis_client", _UnavailableAuthRedis())

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={
            "reason": "Lost device during throttle outage",
            "current_password": "AdminPass123!",
            "code": "123456",
        },
        headers={"x-csrf-token": csrf},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "No administrator MFA code was checked" in response.json()["detail"]
    assert (
        db_session.scalar(
            select(UserTOTPCredential).where(UserTOTPCredential.user_id == viewer.id)
        )
        is not None
    )
    failure = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "users.mfa.reset",
            AuditLog.resource_id == str(viewer.id),
            AuditLog.success.is_(False),
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert failure is not None
    assert failure.actor_user_id == admin.id
    assert failure.metadata_json["reason"] == "throttle_unavailable"


def test_oidc_recent_external_mfa_assurance_can_authorize_admin_mfa_recovery(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    admin.provisioning_source = PROVISIONING_SOURCE_OIDC
    admin.password_login_enabled = False
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, viewer)
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=enrollment,
        session=enrollment_session,
    )
    oidc_session = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_method="oidc",
        mfa_method="external",
        identity_amr=["pwd", "mfa"],
        client_ip="testclient",
        user_agent="pytest",
        now=datetime.now(timezone.utc),
        identity_authenticated_at=datetime.now(timezone.utc),
    )
    db_session.commit()
    client.cookies.set("threatlens_session", oidc_session.token)
    client.cookies.set("threatlens_csrf", "csrf-test")

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={"reason": "Verified support recovery"},
        headers={"x-csrf-token": "csrf-test"},
    )
    assert response.status_code == 200, response.text


def test_plain_oidc_login_cannot_authorize_admin_mfa_recovery(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    admin.provisioning_source = PROVISIONING_SOURCE_OIDC
    admin.password_login_enabled = False
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, viewer)
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=enrollment,
        session=enrollment_session,
    )
    oidc_session = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_method="oidc",
        mfa_method=None,
        identity_authenticated_at=datetime.now(timezone.utc),
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set(
        "threatlens_session", oidc_session.token, domain="testserver.local"
    )
    client.cookies.set("threatlens_csrf", "csrf-test", domain="testserver.local")

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={"reason": "Unverified SSO recovery"},
        headers={"x-csrf-token": "csrf-test"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "oidc_reauthentication_required"


def test_hybrid_admin_mfa_recovery_uses_current_oidc_session_assurance(
    client: TestClient,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    viewer = seed_users["viewer"]
    assert admin.provisioning_source == PROVISIONING_SOURCE_LOCAL
    assert admin.password_login_enabled is True
    enrollment, enrollment_session = _start_direct_totp_enrollment(db_session, viewer)
    _confirm_direct_totp_enrollment(
        db_session,
        user=viewer,
        enrollment=enrollment,
        session=enrollment_session,
    )
    oidc_session = create_auth_session(
        db_session,
        user_id=admin.id,
        auth_method="oidc",
        mfa_method="external",
        identity_amr=["pwd", "mfa"],
        identity_authenticated_at=datetime.now(timezone.utc),
        client_ip="testclient",
        user_agent="pytest",
    )
    db_session.commit()
    client.cookies.set("threatlens_session", oidc_session.token)
    client.cookies.set("threatlens_csrf", "csrf-test")

    response = client.post(
        f"/users/{viewer.id}/mfa/reset",
        json={"reason": "Verified hybrid-account support recovery"},
        headers={"x-csrf-token": "csrf-test"},
    )

    assert response.status_code == 200, response.text
