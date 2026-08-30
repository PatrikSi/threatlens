from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.deps import get_current_principal
from app.core.api_errors import ApiHTTPException
from app.models.audit_log import AuditLog
from app.models.iam import IAMRole, IAMRolePermission
from app.models.service_account import ServiceAccountCredential
from app.models.user import User
from app.schemas.service_account import (
    ServiceAccountCreateRequest,
    ServiceAccountCredentialIssueRequest,
    ServiceAccountRoleAssignmentRequest,
)
from app.services.authorization import (
    authorization_context_for_service_account,
    authorization_context_for_user,
    bump_iam_policy_revision,
)
from app.services import authorization as authorization_service
from app.services.service_accounts import (
    add_role_assignment,
    create_service_account,
    issue_credential,
)


def _issue_service_account_token(
    db: Session,
    actor: User,
    *,
    permissions: list[str],
    scopes: list[str],
):
    suffix = uuid.uuid4().hex[:12]
    actor_authorization = authorization_context_for_user(db, actor)
    account = create_service_account(
        db,
        payload=ServiceAccountCreateRequest(
            key=f"test-agent-{suffix}",
            name="Test automation agent",
        ),
        actor_user_id=actor.id,
    )
    role = IAMRole(
        key=f"test-agent-role-{suffix}",
        name="Test automation role",
        description="Service-account authentication test role",
        is_system=False,
        revision=1,
        created_by_user_id=actor.id,
    )
    db.add(role)
    db.flush()
    db.add_all(
        IAMRolePermission(role_id=role.id, permission=permission)
        for permission in permissions
    )
    db.flush()
    assignment = add_role_assignment(
        db,
        service_account_id=account.id,
        payload=ServiceAccountRoleAssignmentRequest(
            role_id=role.id,
            expected_service_account_revision=account.revision,
            expected_role_revision=role.revision,
        ),
        actor_user_id=actor.id,
        actor_authorization=actor_authorization,
    )
    issued = issue_credential(
        db,
        service_account_id=account.id,
        payload=ServiceAccountCredentialIssueRequest(
            expected_service_account_revision=account.revision,
            name="Test automation token",
            scopes=scopes,
            expires_in_days=30,
        ),
        actor_user_id=actor.id,
        actor_authorization=actor_authorization,
        idempotency_key=f"auth-test-issue-{suffix}",
    )
    db.commit()
    return account, assignment, issued.credential, issued.token


def test_service_account_header_auth_is_attenuated_and_audited(
    client: TestClient,
    db_session: Session,
    seed_users,
):
    account, _assignment, credential, token = _issue_service_account_token(
        db_session,
        seed_users["admin"],
        permissions=["read:items", "write:feeds", "read:stats"],
        scopes=["read:items", "write:feeds", "read:stats"],
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "ThreatLens service-account test/1.0",
        "X-Request-ID": "service-account-auth-test",
    }

    items_response = client.get("/items?page_size=1", headers=headers)
    assert items_response.status_code == 200
    assert all(
        item["personal_state_available"] is False
        for item in items_response.json()["items"]
    )
    unsupported_item_state = client.get(
        "/items?page_size=1&is_starred=true", headers=headers
    )
    assert unsupported_item_state.status_code == 400
    assert (
        unsupported_item_state.json()["error"]["code"]
        == "service_account_user_state_unsupported"
    )
    preview_response = client.post(
        "/exports/preview",
        headers=headers,
        json={"filters": {}},
    )
    assert preview_response.status_code == 200
    assert all(
        item["personal_state_available"] is False
        for item in preview_response.json()["items"]
    )
    unsupported_export_state = client.post(
        "/exports/preview",
        headers=headers,
        json={"filters": {"is_read": True}},
    )
    assert unsupported_export_state.status_code == 400
    assert (
        unsupported_export_state.json()["error"]["code"]
        == "service_account_user_state_unsupported"
    )
    assert client.get("/stats/overview", headers=headers).status_code == 200
    created_feed = client.post(
        "/feeds",
        headers=headers,
        json={
            "name": "Service-account test feed",
            "url": f"https://example.com/{uuid.uuid4().hex}.xml",
            "enabled": False,
        },
    )
    assert created_feed.status_code == 201, created_feed.text

    human_only = client.get("/users", headers=headers)
    assert human_only.status_code == 403
    assert human_only.json()["error"]["code"] == "human_principal_required"

    control_plane = client.get("/iam/service-accounts", headers=headers)
    assert control_plane.status_code == 403
    assert control_plane.json()["error"]["code"] == "permission_denied"

    db_session.expire_all()
    stored_credential = db_session.get(ServiceAccountCredential, credential.id)
    assert stored_credential is not None
    assert stored_credential.last_used_at is not None
    assert (
        stored_credential.last_used_user_agent == "ThreatLens service-account test/1.0"
    )

    feed_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "feeds.create",
            AuditLog.resource_id == created_feed.json()["id"],
        )
    )
    assert feed_audit is not None
    assert feed_audit.actor_user_id is None
    assert feed_audit.actor_principal_type == "service_account"
    assert feed_audit.actor_principal_id == account.id
    assert feed_audit.credential_kind == "service_account_token"
    assert feed_audit.credential_id == credential.id

    denial_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "authorization.permission_denied",
            AuditLog.request_id == "service-account-auth-test",
            AuditLog.actor_principal_id == account.id,
        )
    )
    assert denial_audit is not None
    assert denial_audit.actor_user_id is None
    assert denial_audit.credential_id == credential.id

    client.cookies.set("threatlens_session", token)
    cookie_attempt = client.get("/items?page_size=1")
    client.cookies.delete("threatlens_session")
    assert cookie_attempt.status_code == 401


def test_service_account_role_reduction_and_disable_take_effect_immediately(
    client: TestClient,
    db_session: Session,
    seed_users,
):
    account, assignment, _credential, token = _issue_service_account_token(
        db_session,
        seed_users["admin"],
        permissions=["read:items"],
        scopes=["read:items"],
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/items?page_size=1", headers=headers).status_code == 200

    db_session.delete(assignment)
    bump_iam_policy_revision(db_session)
    db_session.commit()
    reduced = client.get("/items?page_size=1", headers=headers)
    assert reduced.status_code == 403
    assert reduced.json()["error"]["code"] == "permission_denied"

    db_session.refresh(account)
    account.is_active = False
    account.disabled_at = datetime.now(timezone.utc)
    account.revision += 1
    bump_iam_policy_revision(db_session)
    db_session.commit()
    disabled = client.get("/items?page_size=1", headers=headers)
    assert disabled.status_code == 401


def test_service_account_expired_and_revoked_credentials_are_rejected(
    client: TestClient,
    db_session: Session,
    seed_users,
):
    _account, _assignment, expired_credential, expired_token = (
        _issue_service_account_token(
            db_session,
            seed_users["admin"],
            permissions=["read:items"],
            scopes=["read:items"],
        )
    )
    now = datetime.now(timezone.utc)
    expired_credential.created_at = now - timedelta(days=2)
    expired_credential.expires_at = now - timedelta(days=1)
    db_session.add(expired_credential)
    db_session.commit()
    assert (
        client.get(
            "/items?page_size=1",
            headers={"Authorization": f"Bearer {expired_token}"},
        ).status_code
        == 401
    )

    _account, _assignment, revoked_credential, revoked_token = (
        _issue_service_account_token(
            db_session,
            seed_users["admin"],
            permissions=["read:items"],
            scopes=["read:items"],
        )
    )
    revoked_credential.revoked_at = now
    db_session.add(revoked_credential)
    db_session.commit()
    assert (
        client.get(
            "/items?page_size=1",
            headers={"Authorization": f"Bearer {revoked_token}"},
        ).status_code
        == 401
    )


def test_service_account_revocation_inside_policy_snapshot_retries_closed(
    db_session: Session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    account, _assignment, credential, _token = _issue_service_account_token(
        db_session,
        seed_users["admin"],
        permissions=["read:items"],
        scopes=["read:items"],
    )
    original_policy_revision = authorization_service._policy_revision
    revision_reads = 0

    def revision_with_concurrent_revoke(db: Session) -> int:
        nonlocal revision_reads
        revision_reads += 1
        if revision_reads == 2:
            credential.revoked_at = datetime.now(timezone.utc)
            db.add(credential)
            bump_iam_policy_revision(db)
        return original_policy_revision(db)

    monkeypatch.setattr(
        authorization_service, "_policy_revision", revision_with_concurrent_revoke
    )
    context = authorization_context_for_service_account(
        db_session,
        account,
        credential_id=credential.id,
        credential_scopes=["read:items"],
    )

    assert revision_reads == 4
    assert context.account_eligible is False
    assert context.has("read:items") is False


def test_service_account_auth_database_failure_returns_retryable_503():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 43123),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    class FailingSession:
        def scalar(self, *_args, **_kwargs):
            raise OperationalError("SELECT", {}, RuntimeError("database down"))

        def rollback(self):
            return None

    with pytest.raises(ApiHTTPException) as raised:
        get_current_principal(
            request,
            db=FailingSession(),  # type: ignore[arg-type]
            token="tlsa_0123456789abcdef_test-secret",
        )

    assert raised.value.status_code == 503
    assert raised.value.error_code == "authentication_state_unavailable"
