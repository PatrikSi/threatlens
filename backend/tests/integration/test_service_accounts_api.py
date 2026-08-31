from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.service_accounts import router as service_account_router
from app.core.api_errors import install_api_error_handlers
from app.core.rbac import ROLE_VIEWER
from app.core.security import generate_api_token, get_password_hash
from app.core.token_scopes import (
    SCOPE_READ_SERVICE_ACCOUNTS,
    SCOPE_WRITE_SERVICE_ACCOUNTS,
)
from app.db.session import get_db
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.models.iam import (
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.models.service_account import (
    ServiceAccount,
    ServiceAccountCredential,
)
from app.models.user import User
from app.schemas.iam import RoleUpdateRequest
from app.services.iam_roles import (
    IAMRoleConflict,
    delete_role,
    get_role_response,
    update_role,
)
from app.services.service_accounts import SAFE_SERVICE_ACCOUNT_PERMISSIONS


def test_service_account_allowlist_excludes_control_plane_authority():
    forbidden = {
        "read:views",
        "write:views",
        "read:tokens",
        "write:tokens",
        "read:users",
        "write:users",
        "read:iam",
        "write:iam",
        "read:workspace",
        "write:workspace_preferences",
        "write:workspace",
        "read:service_accounts",
        "write:service_accounts",
        "read:elevations",
        "write:elevations",
        "approve:elevations",
        "read:approvals",
        "write:approvals",
        "approve:approvals",
        "read:access_reviews",
        "write:access_reviews",
        "read:data_policies",
        "write:data_policies",
        "write:operations",
        "*:*",
        "read:*",
        "write:*",
        "admin:*",
    }
    assert SAFE_SERVICE_ACCOUNT_PERMISSIONS.isdisjoint(forbidden)
    assert all("*" not in permission for permission in SAFE_SERVICE_ACCOUNT_PERMISSIONS)


@pytest.fixture()
def service_account_api(db_session: Session):
    manager = User(
        id=uuid.uuid4(),
        email=f"service-account-manager-{uuid.uuid4().hex}@example.com",
        password_hash=get_password_hash("ServiceAccountManager123!"),
        role=ROLE_VIEWER,
        is_active=True,
        is_approved=True,
    )
    manager_role = IAMRole(
        id=uuid.uuid4(),
        key=f"service-account-manager-{uuid.uuid4().hex[:12]}",
        name="Service account manager",
        description="Focused test manager",
        is_system=False,
        revision=1,
        created_by_user_id=manager.id,
    )
    db_session.add(manager)
    db_session.flush()
    db_session.add(manager_role)
    db_session.flush()
    db_session.add_all(
        [
            IAMRolePermission(
                role_id=manager_role.id, permission=SCOPE_READ_SERVICE_ACCOUNTS
            ),
            IAMRolePermission(
                role_id=manager_role.id, permission=SCOPE_WRITE_SERVICE_ACCOUNTS
            ),
            IAMRolePermission(role_id=manager_role.id, permission="read:items"),
            IAMUserRoleAssignment(
                user_id=manager.id,
                role_id=manager_role.id,
                source="local",
                source_key="",
                assigned_by_user_id=manager.id,
            ),
        ]
    )
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=manager.id,
            name="service-account-manager-test",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=[
                SCOPE_READ_SERVICE_ACCOUNTS,
                SCOPE_WRITE_SERVICE_ACCOUNTS,
                "read:items",
            ],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()

    application = FastAPI()
    install_api_error_handlers(application)

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get(
            "X-Request-ID", f"service-account-test-{uuid.uuid4()}"
        )
        return await call_next(request)

    application.include_router(service_account_router, prefix="/v1")

    def override_get_db():
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield {
            "client": client,
            "db": db_session,
            "manager": manager,
            "headers": {"Authorization": f"Bearer {token_value}"},
        }


def _create_account(api, *, key: str = "collector-agent") -> dict:
    response = api["client"].post(
        "/v1/iam/service-accounts",
        headers=api["headers"],
        json={
            "key": key,
            "name": "Collector agent",
            "description": "Collects selected intelligence",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_custom_role(
    db: Session,
    manager: User,
    *,
    key: str,
    permissions: list[str],
    is_system: bool = False,
) -> IAMRole:
    role = IAMRole(
        id=uuid.uuid4(),
        key=f"{key}-{uuid.uuid4().hex[:8]}",
        name=key.replace("-", " ").title(),
        description="Service-account test role",
        is_system=is_system,
        revision=1,
        created_by_user_id=manager.id,
    )
    db.add(role)
    db.flush()
    db.add_all(
        IAMRolePermission(role_id=role.id, permission=permission)
        for permission in permissions
    )
    db.commit()
    return role


def _assign_role(api, account: dict, role: IAMRole):
    return api["client"].post(
        f"/v1/iam/service-accounts/{account['id']}/role-assignments",
        headers=api["headers"],
        json={
            "role_id": str(role.id),
            "expected_service_account_revision": account["revision"],
            "expected_role_revision": role.revision,
        },
    )


def _credential_payload(revision: int, *, scopes: list[str]) -> dict:
    return {
        "expected_service_account_revision": revision,
        "name": "collector credential",
        "scopes": scopes,
        "expires_in_days": 30,
    }


def _credential_headers(api, key: str) -> dict[str, str]:
    return {**api["headers"], "Idempotency-Key": key}


def test_service_account_crud_permissions_revisions_and_audits(service_account_api):
    api = service_account_api
    client: TestClient = api["client"]
    db: Session = api["db"]
    account = _create_account(api)
    assert account["revision"] == 1
    assert account["is_active"] is True
    assert account["role_ids"] == []

    listed = client.get("/v1/iam/service-accounts", headers=api["headers"])
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert [row["id"] for row in listed.json()["items"]] == [account["id"]]
    fetched = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    )
    assert fetched.status_code == 200

    updated = client.patch(
        f"/v1/iam/service-accounts/{account['id']}",
        headers=api["headers"],
        json={
            "expected_revision": account["revision"],
            "name": "Renamed collector",
        },
    )
    assert updated.status_code == 200
    account = updated.json()
    assert account["revision"] == 2
    assert account["name"] == "Renamed collector"

    stale = client.patch(
        f"/v1/iam/service-accounts/{account['id']}",
        headers=api["headers"],
        json={"expected_revision": 1, "description": "stale update"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "service_account_revision_conflict"
    assert stale.json()["error"]["context"]["current_revision"] == 2

    duplicate = client.post(
        "/v1/iam/service-accounts",
        headers=api["headers"],
        json={"key": account["key"], "name": "Duplicate"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "service_account_conflict"

    missing = client.get(
        f"/v1/iam/service-accounts/{uuid.uuid4()}", headers=api["headers"]
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "service_account_not_found"

    read_only_token, prefix, token_hash = generate_api_token()
    db.add(
        ApiToken(
            user_id=api["manager"].id,
            name="read-only-service-accounts",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=[SCOPE_READ_SERVICE_ACCOUNTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()
    read_only_headers = {"Authorization": f"Bearer {read_only_token}"}
    assert (
        client.get("/v1/iam/service-accounts", headers=read_only_headers).status_code
        == 200
    )
    denied = client.post(
        "/v1/iam/service-accounts",
        headers=read_only_headers,
        json={"key": "denied-agent", "name": "Denied agent"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"

    audits = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    [
                        "service_accounts.create",
                        "service_accounts.update",
                        "authorization.permission_denied",
                    ]
                )
            )
            .order_by(AuditLog.created_at)
        ).all()
    )
    assert any(
        log.action == "service_accounts.create" and log.success for log in audits
    )
    assert any(
        log.action == "service_accounts.update" and log.success for log in audits
    )
    assert any(
        log.action == "service_accounts.update"
        and not log.success
        and log.metadata_json["reason"] == "service_account_revision_conflict"
        for log in audits
    )
    assert any(
        log.action == "authorization.permission_denied" and not log.success
        for log in audits
    )


def test_roles_credentials_rotation_revocation_disable_and_secrecy(
    service_account_api,
):
    api = service_account_api
    client: TestClient = api["client"]
    db: Session = api["db"]
    account = _create_account(api, key="delivery-agent")
    machine_role = _create_custom_role(
        db,
        api["manager"],
        key="machine-reader",
        permissions=["read:items"],
    )
    assigned = _assign_role(api, account, machine_role)
    assert assigned.status_code == 201, assigned.text
    assert assigned.headers["X-Current-Revision"] == "2"
    assignment = assigned.json()
    listed_assignments = client.get(
        f"/v1/iam/service-accounts/{account['id']}/role-assignments",
        headers=api["headers"],
    )
    assert listed_assignments.status_code == 200
    assert [row["id"] for row in listed_assignments.json()] == [assignment["id"]]
    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert account["revision"] == 2
    assert account["effective_permissions"] == ["read:items"]
    assert get_role_response(db, machine_role.id).assignment_count == 1
    with pytest.raises(IAMRoleConflict, match="assigned to service accounts"):
        update_role(
            db,
            role_id=machine_role.id,
            payload=RoleUpdateRequest(
                expected_revision=machine_role.revision,
                permissions=["read:items", "write:iam"],
            ),
        )
    with pytest.raises(IAMRoleConflict, match="still assigned"):
        delete_role(
            db,
            role_id=machine_role.id,
            expected_revision=machine_role.revision,
        )

    escalation_role = _create_custom_role(
        db,
        api["manager"],
        key="machine-escalation",
        permissions=["write:feeds"],
    )
    rejected_escalation = _assign_role(api, account, escalation_role)
    assert rejected_escalation.status_code == 403
    assert (
        rejected_escalation.json()["error"]["code"]
        == "service_account_delegation_denied"
    )
    assert rejected_escalation.json()["error"]["context"]["missing_permissions"] == [
        "write:feeds"
    ]

    unsafe_role = _create_custom_role(
        db,
        api["manager"],
        key="machine-unsafe",
        permissions=["write:iam"],
    )
    rejected_unsafe = _assign_role(api, account, unsafe_role)
    assert rejected_unsafe.status_code == 409
    assert (
        rejected_unsafe.json()["error"]["code"]
        == "service_account_role_unsafe_permissions"
    )
    assert rejected_unsafe.json()["error"]["context"]["blocked_permissions"] == [
        "write:iam"
    ]

    system_role = _create_custom_role(
        db,
        api["manager"],
        key="sealed-machine-role",
        permissions=["read:items"],
        is_system=True,
    )
    rejected_system = _assign_role(api, account, system_role)
    assert rejected_system.status_code == 409
    assert (
        rejected_system.json()["error"]["code"]
        == "service_account_system_role_rejected"
    )

    wildcard_role = _create_custom_role(
        db,
        api["manager"],
        key="wildcard-machine-role",
        permissions=["*:*"],
    )
    rejected_wildcard = _assign_role(api, account, wildcard_role)
    assert rejected_wildcard.status_code == 409
    assert (
        rejected_wildcard.json()["error"]["code"]
        == "service_account_role_contains_wildcard"
    )

    unsafe_scope = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "unsafe-scope-request"),
        json=_credential_payload(account["revision"], scopes=["write:iam"]),
    )
    assert unsafe_scope.status_code == 400
    assert unsafe_scope.json()["error"]["code"] == "service_account_scope_not_allowed"

    ungranted_scope = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "ungranted-scope-request"),
        json=_credential_payload(account["revision"], scopes=["read:feeds"]),
    )
    assert ungranted_scope.status_code == 403
    assert ungranted_scope.json()["error"]["code"] == "service_account_scope_escalation"

    empty_scope = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "empty-scope-request"),
        json=_credential_payload(account["revision"], scopes=[]),
    )
    assert empty_scope.status_code == 422
    too_long = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "expiry-too-long-request"),
        json={
            **_credential_payload(account["revision"], scopes=["read:items"]),
            "expires_in_days": 366,
        },
    )
    assert too_long.status_code == 422

    issued = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers={
            **_credential_headers(api, "credential-issue-request-0001"),
            "X-Request-ID": "credential-secret-test",
        },
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert issued.status_code == 201, issued.text
    assert issued.headers["Cache-Control"] == "no-store"
    assert issued.headers["X-Current-Revision"] == "3"
    token = issued.json()["token"]
    public_credential = issued.json()["credential"]
    assert token.startswith("tlsa_")
    assert public_credential["token_prefix"].startswith("tlsa_")
    assert token != public_credential["token_prefix"]
    credential_id = uuid.UUID(public_credential["id"])

    stored = db.get(ServiceAccountCredential, credential_id)
    assert stored is not None
    assert stored.token_hash != token
    assert token not in stored.token_hash
    assert len(stored.token_hash) == 64
    credential_audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "service_accounts.credentials.create",
            AuditLog.resource_id == str(credential_id),
        )
    )
    assert credential_audit is not None
    assert token not in json.dumps(credential_audit.metadata_json)

    replayed_issue = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "credential-issue-request-0001"),
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert replayed_issue.status_code == 409
    assert (
        replayed_issue.json()["error"]["code"]
        == "service_account_credential_issue_already_committed"
    )
    assert replayed_issue.json()["error"]["context"]["credential_id"] == str(
        credential_id
    )
    conflicting_issue = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "credential-issue-request-0001"),
        json={
            **_credential_payload(account["revision"], scopes=["read:items"]),
            "name": "different request",
        },
    )
    assert conflicting_issue.status_code == 409
    assert (
        conflicting_issue.json()["error"]["code"]
        == "service_account_idempotency_conflict"
    )

    credentials = client.get(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=api["headers"],
    )
    assert credentials.status_code == 200
    assert credentials.json()["total"] == 1
    assert "token" not in credentials.json()["items"][0]
    assert "token_hash" not in credentials.json()["items"][0]

    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert account["revision"] == 3
    blank_rotation_key = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{credential_id}/rotate",
        headers={**api["headers"], "Idempotency-Key": "        "},
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert blank_rotation_key.status_code == 400
    assert (
        blank_rotation_key.json()["error"]["code"]
        == "service_account_idempotency_key_invalid"
    )
    rotated = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{credential_id}/rotate",
        headers={**api["headers"], "Idempotency-Key": "rotation-request-0001"},
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert rotated.status_code == 201, rotated.text
    rotated_token = rotated.json()["token"]
    rotated_credential = rotated.json()["credential"]
    assert rotated_token != token
    assert rotated_credential["rotated_from_credential_id"] == str(credential_id)
    assert rotated.json()["previous_credential_id"] == str(credential_id)
    assert rotated.json()["previous_credential_revoked"] is False
    previous_expiry = datetime.fromisoformat(
        rotated.json()["previous_credential_expires_at"]
    )
    assert previous_expiry <= datetime.now(timezone.utc) + timedelta(
        hours=24, minutes=1
    )
    assert rotated.headers["X-Current-Revision"] == "4"
    db.refresh(stored)
    assert stored.revoked_at is None
    assert stored.original_expires_at is not None
    assert stored.original_expires_at > stored.expires_at

    repeated_rotation = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{credential_id}/rotate",
        headers={**api["headers"], "Idempotency-Key": "rotation-request-0001"},
        json=_credential_payload(account["revision"] + 1, scopes=["read:items"]),
    )
    assert repeated_rotation.status_code == 409
    assert (
        repeated_rotation.json()["error"]["code"]
        == "service_account_rotation_already_committed"
    )
    conflicting_rotation = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{credential_id}/rotate",
        headers={**api["headers"], "Idempotency-Key": "rotation-request-0001"},
        json={
            **_credential_payload(account["revision"] + 1, scopes=["read:items"]),
            "name": "different rotation",
        },
    )
    assert conflicting_rotation.status_code == 409
    assert (
        conflicting_rotation.json()["error"]["code"]
        == "service_account_idempotency_conflict"
    )

    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert account["revision"] == 4
    revoked_previous = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{credential_id}/revoke",
        headers=api["headers"],
        json={"expected_revision": account["revision"]},
    )
    assert revoked_previous.status_code == 200
    assert revoked_previous.headers["X-ThreatLens-Mutation-Changed"] == "true"
    assert revoked_previous.headers["X-Current-Revision"] == "5"

    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    rotated_id = rotated_credential["id"]
    revoked = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{rotated_id}/revoke",
        headers=api["headers"],
        json={"expected_revision": account["revision"]},
    )
    assert revoked.status_code == 200
    revoked_at = revoked.json()["revoked_at"]
    assert revoked_at is not None

    repeated_revoke = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials/{rotated_id}/revoke",
        headers=api["headers"],
        json={"expected_revision": account["revision"]},
    )
    assert repeated_revoke.status_code == 200
    assert repeated_revoke.json()["revoked_at"] == revoked_at
    assert repeated_revoke.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert repeated_revoke.headers["X-Current-Revision"] == "6"
    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert account["revision"] == 6

    active_credential = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "active-credential-request"),
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert active_credential.status_code == 201
    active_credential_id = uuid.UUID(active_credential.json()["credential"]["id"])
    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert account["revision"] == 7

    disabled = client.post(
        f"/v1/iam/service-accounts/{account['id']}/disable",
        headers=api["headers"],
        json={"expected_revision": account["revision"]},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert disabled.json()["revision"] == 8
    assert disabled.headers["X-ThreatLens-Mutation-Changed"] == "true"
    active_stored = db.get(ServiceAccountCredential, active_credential_id)
    db.refresh(active_stored)
    assert active_stored.revoked_at is not None

    repeated_disable = client.post(
        f"/v1/iam/service-accounts/{account['id']}/disable",
        headers=api["headers"],
        json={"expected_revision": 7},
    )
    assert repeated_disable.status_code == 200
    assert repeated_disable.json()["revision"] == 8
    assert repeated_disable.headers["X-ThreatLens-Mutation-Changed"] == "false"

    denied_after_disable = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "disabled-credential-request"),
        json=_credential_payload(8, scopes=["read:items"]),
    )
    assert denied_after_disable.status_code == 409
    assert denied_after_disable.json()["error"]["code"] == "service_account_inactive"

    remove_assignment = client.delete(
        f"/v1/iam/service-accounts/{account['id']}/role-assignments/{assignment['id']}",
        headers=api["headers"],
        params={"expected_revision": 8},
    )
    assert remove_assignment.status_code == 204
    final_account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    assert final_account["revision"] == 9
    assert final_account["role_ids"] == []
    deleted = client.delete(
        f"/v1/iam/service-accounts/{account['id']}",
        headers=api["headers"],
        params={"expected_revision": final_account["revision"]},
    )
    assert deleted.status_code == 204
    assert (
        client.get(
            f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
        ).status_code
        == 404
    )


def test_stale_role_assignment_and_last_used_metadata_are_explicit(
    service_account_api,
):
    api = service_account_api
    client: TestClient = api["client"]
    db: Session = api["db"]
    account = _create_account(api, key="metadata-agent")
    first_role = _create_custom_role(
        db,
        api["manager"],
        key="metadata-reader",
        permissions=["read:items"],
    )
    second_role = _create_custom_role(
        db,
        api["manager"],
        key="metadata-stats",
        permissions=["read:stats"],
    )
    assert _assign_role(api, account, first_role).status_code == 201
    stale_assignment = _assign_role(api, account, second_role)
    assert stale_assignment.status_code == 409
    assert (
        stale_assignment.json()["error"]["code"] == "service_account_revision_conflict"
    )

    account = client.get(
        f"/v1/iam/service-accounts/{account['id']}", headers=api["headers"]
    ).json()
    issued = client.post(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=_credential_headers(api, "pagination-credential-request"),
        json=_credential_payload(account["revision"], scopes=["read:items"]),
    )
    assert issued.status_code == 201
    credential_id = uuid.UUID(issued.json()["credential"]["id"])
    credential = db.get(ServiceAccountCredential, credential_id)
    used_at = datetime.now(timezone.utc)
    credential.last_used_at = used_at
    credential.last_used_ip = "192.0.2.10"
    credential.last_used_user_agent = "ThreatLens test collector/1.0"
    db.add(credential)
    db.commit()

    listed = client.get(
        f"/v1/iam/service-accounts/{account['id']}/credentials",
        headers=api["headers"],
    )
    assert listed.status_code == 200
    metadata = listed.json()["items"][0]
    assert metadata["last_used_at"] is not None
    assert metadata["last_used_ip"] == "192.0.2.10"
    assert metadata["last_used_user_agent"] == "ThreatLens test collector/1.0"
    assert "token" not in metadata
    assert "token_hash" not in metadata

    stored_account = db.get(ServiceAccount, uuid.UUID(account["id"]))
    assert stored_account.revision == 3
