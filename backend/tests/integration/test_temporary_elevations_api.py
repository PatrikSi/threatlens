from __future__ import annotations

from sqlalchemy import select

from app.models.iam import IAMPolicyState
from app.models.audit_log import AuditLog
from app.models.governance_operation_receipt import GovernanceOperationReceipt


def _create_role(client, admin_headers, *, key: str, permissions: list[str]):
    response = client.post(
        "/iam/roles",
        headers=admin_headers,
        json={
            "key": key,
            "name": key.replace("-", " ").title(),
            "permissions": permissions,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assign_role(client, admin_headers, *, user_id, role):
    response = client.post(
        f"/iam/users/{user_id}/role-assignments",
        headers=admin_headers,
        json={
            "role_id": role["id"],
            "expected_role_revision": role["revision"],
        },
    )
    assert response.status_code == 201, response.text


def _browser_login(client, *, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_elevation_lifecycle_is_idempotent_audited_and_immediately_enforced(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    approver_role = _create_role(
        client,
        auth_headers["admin"],
        key="elevation-approver",
        permissions=[
            "read:elevations",
            "write:elevations",
            "approve:elevations",
        ],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["analyst"].id,
        role=approver_role,
    )
    temporary_requester_role = _create_role(
        client,
        auth_headers["admin"],
        key="temporary-requester",
        permissions=["read:elevations", "write:elevations"],
    )
    temporary_audit_role = _create_role(
        client,
        auth_headers["admin"],
        key="temporary-audit-reader",
        permissions=["read:audit"],
    )

    create_payload = {
        "target_user_id": str(seed_users["viewer"].id),
        "role_id": temporary_requester_role["id"],
        "expected_role_revision": temporary_requester_role["revision"],
        "duration_seconds": 3600,
        "reason": "Triage an active incident with bounded access.",
    }
    create_headers = {
        **auth_headers["admin"],
        "Idempotency-Key": "elevation-create-lifecycle-1",
    }
    created = client.post(
        "/iam/elevations", headers=create_headers, json=create_payload
    )
    assert created.status_code == 201, created.text
    elevation = created.json()
    assert elevation["status"] == "pending"
    assert elevation["permission_snapshot"] == [
        "read:elevations",
        "write:elevations",
    ]
    assert created.headers["X-ThreatLens-Mutation-Changed"] == "true"

    replay = client.post("/iam/elevations", headers=create_headers, json=create_payload)
    assert replay.status_code == 201
    assert replay.json()["id"] == elevation["id"]
    assert replay.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert (
        db_session.scalar(
            select(GovernanceOperationReceipt).where(
                GovernanceOperationReceipt.resource_id == elevation["id"]
            )
        )
        is not None
    )

    changed_payload = {**create_payload, "duration_seconds": 7200}
    conflict = client.post(
        "/iam/elevations", headers=create_headers, json=changed_payload
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "governance_idempotency_conflict"

    before = client.get("/iam/effective", headers=auth_headers["viewer"])
    assert before.status_code == 200
    assert "write:elevations" not in before.json()["permissions"]

    analyst_browser = _browser_login(
        client,
        email="analyst@example.com",
        password="AnalystPass123!",
    )
    decision_headers = {
        **analyst_browser,
        "Idempotency-Key": "elevation-decision-lifecycle-1",
    }
    decision_payload = {
        "expected_revision": elevation["revision"],
        "approve": True,
        "reason": "Approved for the current incident response window.",
    }
    approved = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers=decision_headers,
        json=decision_payload,
    )
    assert approved.status_code == 200, approved.text
    grant = approved.json()
    assert grant["status"] == "approved"
    assert grant["grant_expires_at"]

    decision_replay = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers=decision_headers,
        json=decision_payload,
    )
    assert decision_replay.status_code == 200
    assert decision_replay.headers["X-ThreatLens-Mutation-Changed"] == "false"

    after = client.get("/iam/effective", headers=auth_headers["viewer"])
    assert after.status_code == 200
    assert "write:elevations" in after.json()["permissions"]
    assert elevation["id"] in after.json()["elevation_ids"]
    assert any(
        role["source"] == f"elevation:{elevation['id']}"
        for role in after.json()["roles"]
    )

    blocked_role_update = client.patch(
        f"/iam/roles/{temporary_requester_role['id']}",
        headers=auth_headers["admin"],
        json={
            "expected_revision": temporary_requester_role["revision"],
            "permissions": [
                "read:elevations",
                "write:elevations",
                "read:reports",
            ],
        },
    )
    assert blocked_role_update.status_code == 409
    assert blocked_role_update.json()["error"]["code"] == "iam_role_conflict"

    elevated_request = client.post(
        "/iam/elevations",
        headers={
            **auth_headers["viewer"],
            "Idempotency-Key": "elevated-followup-request-1",
            "X-Request-ID": "elevated-followup-audit",
        },
        json={
            "role_id": temporary_audit_role["id"],
            "expected_role_revision": temporary_audit_role["revision"],
            "duration_seconds": 900,
            "reason": "Review audit evidence associated with the active incident.",
        },
    )
    assert elevated_request.status_code == 201, elevated_request.text
    followup = elevated_request.json()
    audit_entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "elevations.request.create",
            AuditLog.request_id == "elevated-followup-audit",
        )
    )
    assert audit_entry is not None
    assert audit_entry.authorization_elevation_ids == [elevation["id"]]
    assert audit_entry.metadata_json["authorization_elevation_ids"] == [elevation["id"]]

    filtered_audit = client.get(
        f"/audit-logs?elevation_id={elevation['id']}",
        headers=auth_headers["admin"],
    )
    assert filtered_audit.status_code == 200
    assert any(
        row["request_id"] == "elevated-followup-audit"
        for row in filtered_audit.json()["logs"]
    )

    token_close = client.post(
        f"/iam/elevations/{elevation['id']}/close",
        headers={
            **auth_headers["viewer"],
            "Idempotency-Key": "elevation-close-token-denied-1",
        },
        json={
            "expected_revision": grant["revision"],
            "reason": "Incident work is complete.",
        },
    )
    assert token_close.status_code == 403
    assert token_close.json()["error"]["code"] == "browser_session_required"

    viewer_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    closed = client.post(
        f"/iam/elevations/{elevation['id']}/close",
        headers={
            **viewer_browser,
            "Idempotency-Key": "elevation-close-lifecycle-1",
        },
        json={
            "expected_revision": grant["revision"],
            "reason": "Incident work is complete.",
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "revoked"
    assert closed.json()["closed_by_principal_type"] == "user"

    no_longer_elevated = client.get("/iam/effective", headers=auth_headers["viewer"])
    assert "write:elevations" not in no_longer_elevated.json()["permissions"]
    assert elevation["id"] not in no_longer_elevated.json()["elevation_ids"]

    cancelled = client.post(
        f"/iam/elevations/{followup['id']}/close",
        headers={
            **viewer_browser,
            "Idempotency-Key": "elevation-cancel-followup-1",
        },
        json={
            "expected_revision": followup["revision"],
            "reason": "Audit access is no longer required.",
        },
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_elevation_decision_rejects_requester_target_and_temporary_approver(
    client,
    auth_headers,
    seed_users,
):
    target_role = _create_role(
        client,
        auth_headers["admin"],
        key="temporary-stats-reader",
        permissions=["read:stats"],
    )
    approver_role = _create_role(
        client,
        auth_headers["admin"],
        key="separation-elevation-approver",
        permissions=[
            "read:elevations",
            "write:elevations",
            "approve:elevations",
        ],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=approver_role,
    )
    created = client.post(
        "/iam/elevations",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "elevation-separation-create-1",
        },
        json={
            "target_user_id": str(seed_users["viewer"].id),
            "role_id": target_role["id"],
            "expected_role_revision": target_role["revision"],
            "duration_seconds": 600,
            "reason": "Read statistics during incident response validation.",
        },
    )
    assert created.status_code == 201
    elevation = created.json()

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    self_decision = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers={
            **admin_browser,
            "Idempotency-Key": "elevation-self-decision-1",
        },
        json={
            "expected_revision": elevation["revision"],
            "approve": True,
            "reason": "Attempted self decision.",
        },
    )
    assert self_decision.status_code == 403
    assert self_decision.json()["error"]["code"] == "temporary_elevation_forbidden"

    target_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    target_decision = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers={
            **target_browser,
            "Idempotency-Key": "elevation-target-decision-1",
        },
        json={
            "expected_revision": elevation["revision"],
            "approve": True,
            "reason": "Attempted target decision.",
        },
    )
    assert target_decision.status_code == 403
    assert target_decision.json()["error"]["code"] == "temporary_elevation_forbidden"

    temporary_approver_request = client.post(
        "/iam/elevations",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "temporary-approver-create-1",
        },
        json={
            "target_user_id": str(seed_users["analyst"].id),
            "role_id": approver_role["id"],
            "expected_role_revision": approver_role["revision"],
            "duration_seconds": 600,
            "reason": "Validate that approval authority cannot be chained.",
        },
    )
    assert temporary_approver_request.status_code == 201
    temporary_approver = temporary_approver_request.json()
    approved_temporary_approver = client.post(
        f"/iam/elevations/{temporary_approver['id']}/decision",
        headers={
            **target_browser,
            "Idempotency-Key": "temporary-approver-decision-1",
        },
        json={
            "expected_revision": temporary_approver["revision"],
            "approve": True,
            "reason": "Approve only to exercise the non-delegation boundary.",
        },
    )
    assert approved_temporary_approver.status_code == 200

    analyst_browser = _browser_login(
        client,
        email="analyst@example.com",
        password="AnalystPass123!",
    )
    temporary_approver_decision = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers={
            **analyst_browser,
            "Idempotency-Key": "temporary-approver-rejected-1",
        },
        json={
            "expected_revision": elevation["revision"],
            "approve": True,
            "reason": "Attempted approval using temporary authority.",
        },
    )
    assert temporary_approver_decision.status_code == 403
    assert (
        temporary_approver_decision.json()["error"]["code"]
        == "temporary_elevation_forbidden"
    )

    still_pending = client.get(
        f"/iam/elevations/{elevation['id']}", headers=auth_headers["admin"]
    )
    assert still_pending.status_code == 200
    assert still_pending.json()["status"] == "pending"


def test_temporary_control_plane_access_cannot_be_made_persistent(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    elevated_permissions = [
        "read:elevations",
        "write:elevations",
        "read:iam",
        "write:iam",
        "read:service_accounts",
        "write:service_accounts",
    ]
    elevated_role = _create_role(
        client,
        auth_headers["admin"],
        key="temporary-control-plane",
        permissions=elevated_permissions,
    )
    durable_approver_role = _create_role(
        client,
        auth_headers["admin"],
        key="durable-control-plane-approver",
        permissions=[*elevated_permissions, "approve:elevations"],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["analyst"].id,
        role=durable_approver_role,
    )

    requested = client.post(
        "/iam/elevations",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "control-plane-elevation-create-1",
        },
        json={
            "target_user_id": str(seed_users["viewer"].id),
            "role_id": elevated_role["id"],
            "expected_role_revision": elevated_role["revision"],
            "duration_seconds": 600,
            "reason": "Exercise control-plane non-persistence safeguards.",
        },
    )
    assert requested.status_code == 201, requested.text
    elevation = requested.json()

    analyst_browser = _browser_login(
        client,
        email="analyst@example.com",
        password="AnalystPass123!",
    )
    approved = client.post(
        f"/iam/elevations/{elevation['id']}/decision",
        headers={
            **analyst_browser,
            "Idempotency-Key": "control-plane-elevation-decision-1",
        },
        json={
            "expected_revision": elevation["revision"],
            "approve": True,
            "reason": "Approve to verify durable authority boundaries.",
        },
    )
    assert approved.status_code == 200, approved.text

    viewer_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    revision_before = int(
        db_session.scalar(select(IAMPolicyState.revision).where(IAMPolicyState.id == 1))
    )
    iam_attempts = [
        client.post(
            "/iam/roles",
            headers=viewer_browser,
            json={
                "key": "persisted-from-elevation",
                "name": "Persisted from elevation",
                "permissions": ["read:stats"],
            },
        ),
        client.post(
            f"/iam/users/{seed_users['viewer'].id}/role-assignments",
            headers=viewer_browser,
            json={
                "role_id": elevated_role["id"],
                "expected_role_revision": elevated_role["revision"],
            },
        ),
        client.post(
            "/iam/groups",
            headers=viewer_browser,
            json={"key": "persisted-group", "name": "Persisted group"},
        ),
    ]
    for attempt in iam_attempts:
        assert attempt.status_code == 403, attempt.text
        assert attempt.json()["error"]["code"] == "iam_durable_authority_required"

    service_account_attempt = client.post(
        "/iam/service-accounts",
        headers=viewer_browser,
        json={
            "key": "persisted-machine",
            "name": "Persisted machine",
        },
    )
    assert service_account_attempt.status_code == 403, service_account_attempt.text
    assert (
        service_account_attempt.json()["error"]["code"]
        == "service_account_durable_authority_required"
    )

    db_session.expire_all()
    revision_after = int(
        db_session.scalar(select(IAMPolicyState.revision).where(IAMPolicyState.id == 1))
    )
    assert revision_after == revision_before
    rejection_audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.actor_user_id == seed_users["viewer"].id,
            AuditLog.action.in_(
                ["iam.authorization.reject", "service_accounts.authorization.reject"]
            ),
        )
    ).all()
    assert len(rejection_audits) == 4
    assert all(
        row.authorization_elevation_ids == [elevation["id"]] for row in rejection_audits
    )
