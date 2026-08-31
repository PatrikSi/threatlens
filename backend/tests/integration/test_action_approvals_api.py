from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.action_approval import (
    ActionApprovalRequest,
    ActionExecutionReceipt,
)
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.audit_log import AuditLog
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.service_account import ServiceAccount


def _create_role(client, admin_headers, *, permissions: list[str]) -> dict:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/iam/roles",
        headers=admin_headers,
        json={
            "key": f"approval-requester-{suffix}",
            "name": f"Approval requester {suffix}",
            "description": "Narrow action-approval integration-test role.",
            "permissions": permissions,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assign_role(client, admin_headers, *, user_id: uuid.UUID, role: dict) -> dict:
    response = client.post(
        f"/iam/users/{user_id}/role-assignments",
        headers=admin_headers,
        json={
            "role_id": role["id"],
            "expected_role_revision": role["revision"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _grant_requester_access(client, admin_headers, *, user_id: uuid.UUID) -> dict:
    role = _create_role(
        client,
        admin_headers,
        permissions=[
            "read:approvals",
            "write:approvals",
            "read:service_accounts",
        ],
    )
    _assign_role(client, admin_headers, user_id=user_id, role=role)
    return role


def _create_service_account(client, admin_headers) -> dict:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/iam/service-accounts",
        headers=admin_headers,
        json={
            "key": f"approval-target-{suffix}",
            "name": f"Approval target {suffix}",
            "description": "Action-approval integration-test target.",
        },
    )
    assert response.status_code == 201, response.text
    account = response.json()
    assert account["is_active"] is True
    return account


def _browser_login(client, *, email: str, password: str) -> dict[str, str]:
    client.cookies.clear()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf_token = response.json().get("csrf_token")
    assert csrf_token
    assert client.cookies.get("threatlens_session")
    return {"X-CSRF-Token": csrf_token}


def _set_auth_token_version(db_session, user, version: int) -> None:
    user.auth_token_version = version
    db_session.add(user)
    db_session.commit()


def _request_payload(account: dict) -> dict:
    return {
        "action_type": "service_account.disable",
        "target_id": account["id"],
        "target_revision": account["revision"],
        "payload": {},
        "expires_in_seconds": 3_600,
        "reason": "Disable this service account after independent review.",
    }


def _create_approval(client, browser_headers, account: dict, *, key: str) -> dict:
    response = client.post(
        "/iam/action-approvals",
        headers={**browser_headers, "Idempotency-Key": key},
        json=_request_payload(account),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client, browser_headers, approval: dict, *, key: str) -> dict:
    response = client.post(
        f"/iam/action-approvals/{approval['id']}/decision",
        headers={**browser_headers, "Idempotency-Key": key},
        json={
            "expected_revision": approval["revision"],
            "approve": True,
            "reason": "The requested disable operation is justified.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_action_catalog_and_history_require_read_approvals(
    client,
    auth_headers,
    seed_users,
):
    anonymous = client.get("/iam/action-approvals/actions")
    assert anonymous.status_code == 401

    denied_catalog = client.get(
        "/iam/action-approvals/actions",
        headers=auth_headers["viewer"],
    )
    assert denied_catalog.status_code == 403
    assert denied_catalog.json()["error"]["code"] == "permission_denied"

    denied_history = client.get(
        "/iam/action-approvals",
        headers=auth_headers["viewer"],
    )
    assert denied_history.status_code == 403
    assert denied_history.json()["error"]["code"] == "permission_denied"

    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    viewer_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )

    catalog = client.get(
        "/iam/action-approvals/actions",
        headers=viewer_browser,
    )
    assert catalog.status_code == 200, catalog.text
    definitions = {item["key"]: item for item in catalog.json()}
    assert set(definitions) == {
        "ai.provider_attempt.acknowledge_may_have_sent",
        "ai.provider_attempt.confirm_not_sent",
        "iam.role.delete",
        "service_account.disable",
    }
    ai_receipt_action = definitions["ai.provider_attempt.confirm_not_sent"]
    assert ai_receipt_action["target_type"] == "ai_provider_attempt_receipt"
    assert ai_receipt_action["requester_permission"] == "read:ai"
    assert ai_receipt_action["approver_permission"] == "write:ai"
    assert ai_receipt_action["risk"] == "critical"
    assert ai_receipt_action["version"] == 1
    assert ai_receipt_action["payload_fields"] == []
    service_account_action = definitions["service_account.disable"]
    assert service_account_action["label"] == "Disable service account"
    assert service_account_action["target_type"] == "service_account"
    assert service_account_action["requester_permission"] == "read:service_accounts"
    assert service_account_action["approver_permission"] == "write:service_accounts"
    assert service_account_action["risk"] == "critical"
    assert service_account_action["version"] == 1
    assert service_account_action["payload_fields"] == []

    history = client.get("/iam/action-approvals", headers=viewer_browser)
    assert history.status_code == 200, history.text
    assert history.json() == {
        "approvals": [],
        "total": 0,
        "page": 1,
        "page_size": 25,
    }


def test_ai_provider_receipt_reconciliation_requires_two_person_approval(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:approvals", "write:approvals", "read:ai"],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=role,
    )
    provider_receipt = AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(),
        attempt_number=1,
        request_fingerprint="c" * 64,
        task_run_id_snapshot=uuid.uuid4(),
        feature_type="item_enrichment",
        resource_type="item",
        resource_id=uuid.uuid4(),
        max_attempts=3,
        requested_max_tokens=1_024,
        iam_revision=2,
        data_policy_revision=3,
        data_policy_mode="enforced",
        state="ambiguous",
        io_outcome="ambiguous",
        retryable=False,
        settled_at=func.now(),
    )
    db_session.add(provider_receipt)
    db_session.commit()
    _set_auth_token_version(db_session, seed_users["admin"], 1)

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    created_response = client.post(
        "/iam/action-approvals",
        headers={
            **requester_browser,
            "Idempotency-Key": "ai-receipt-reconcile-create-1",
        },
        json={
            "action_type": "ai.provider_attempt.acknowledge_may_have_sent",
            "target_id": str(provider_receipt.id),
            "target_revision": 1,
            "payload": {},
            "expires_in_seconds": 3_600,
            "reason": "Acknowledge the independently reviewed provider outcome.",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["requester_permission"] == "read:ai"
    assert created["approver_permission"] == "write:ai"
    assert created["target_snapshot"]["state"] == "ambiguous"
    assert created["target_snapshot"]["io_outcome"] == "ambiguous"
    assert created["target_snapshot"]["request_fingerprint"] == "c" * 64

    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved_response = client.post(
        f"/iam/action-approvals/{created['id']}/decision",
        headers={
            **approver_browser,
            "Idempotency-Key": "ai-receipt-reconcile-decision-1",
        },
        json={
            "expected_revision": 1,
            "approve": True,
            "reason": "Provider evidence was reviewed independently.",
        },
    )
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    assert approved["decided_by_user_id"] == str(seed_users["admin"].id)

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    executed_response = client.post(
        f"/iam/action-approvals/{created['id']}/execute",
        headers={
            **requester_browser,
            "Idempotency-Key": "ai-receipt-reconcile-execute-1",
        },
        json={"expected_revision": approved["revision"]},
    )
    assert executed_response.status_code == 200, executed_response.text
    result = executed_response.json()["receipt"]["result"]
    assert result["reconciliation_action"] == "acknowledged_may_have_sent"
    assert result["reconciled_from_state"] == "ambiguous"
    assert result["reconciled_from_io_outcome"] == "ambiguous"
    assert result["state"] == "ambiguous"
    assert result["io_outcome"] == "ambiguous"
    assert result["retryable"] is False
    assert result["new_revision"] == 2

    db_session.expire_all()
    reconciled = db_session.get(AIProviderAttemptReceipt, provider_receipt.id)
    assert reconciled is not None
    assert reconciled.reconciliation_action == "acknowledged_may_have_sent"
    assert reconciled.reconciled_by_user_id_snapshot == seed_users["viewer"].id
    assert reconciled.reconciled_from_state == "ambiguous"
    assert reconciled.reconciled_from_io_outcome == "ambiguous"


def test_service_account_disable_approval_lifecycle_is_idempotent_and_audited(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    account = _create_service_account(client, auth_headers["admin"])
    _set_auth_token_version(db_session, seed_users["admin"], 1)

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    create_headers = {
        **requester_browser,
        "Idempotency-Key": "action-approval-create-lifecycle-1",
        "X-Request-ID": "action-approval-create-audit",
    }
    create_payload = _request_payload(account)
    created_response = client.post(
        "/iam/action-approvals",
        headers=create_headers,
        json=create_payload,
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["stored_status"] == "pending"
    assert created["status"] == "pending"
    assert created["revision"] == 1
    assert created["requested_by_user_id"] == str(seed_users["viewer"].id)
    assert created["requested_by_email"] == "viewer@example.com"
    assert created["requester_permission"] == "read:service_accounts"
    assert created["approver_permission"] == "write:service_accounts"
    assert created["target_snapshot"]["revision"] == account["revision"]
    assert len(created["payload_digest"]) == 64
    assert created_response.headers["X-ThreatLens-Mutation-Changed"] == "true"
    assert created_response.headers["X-Current-Revision"] == "1"

    create_replay = client.post(
        "/iam/action-approvals",
        headers=create_headers,
        json=create_payload,
    )
    assert create_replay.status_code == 201, create_replay.text
    assert create_replay.json() == created
    assert create_replay.headers["X-ThreatLens-Mutation-Changed"] == "false"

    listed = client.get(
        "/iam/action-approvals?stored_status=pending",
        headers=requester_browser,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["approvals"][0]["id"] == created["id"]

    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    decision_headers = {
        **approver_browser,
        "Idempotency-Key": "action-approval-decision-lifecycle-1",
        "X-Request-ID": "action-approval-decision-audit",
    }
    decision_payload = {
        "expected_revision": created["revision"],
        "approve": True,
        "reason": "The requested disable operation is justified.",
    }
    approved_response = client.post(
        f"/iam/action-approvals/{created['id']}/decision",
        headers=decision_headers,
        json=decision_payload,
    )
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    assert approved["stored_status"] == "approved"
    assert approved["revision"] == 2
    assert approved["decided_by_user_id"] == str(seed_users["admin"].id)
    assert approved["decided_by_email"] == "admin@example.com"
    assert approved["decided_auth_method"] == "local"
    assert approved["decided_mfa_method"] is None
    assert approved["decided_auth_token_version"] == 1
    assert approved_response.headers["X-ThreatLens-Mutation-Changed"] == "true"

    decision_replay = client.post(
        f"/iam/action-approvals/{created['id']}/decision",
        headers=decision_headers,
        json=decision_payload,
    )
    assert decision_replay.status_code == 200, decision_replay.text
    assert decision_replay.json() == approved
    assert decision_replay.headers["X-ThreatLens-Mutation-Changed"] == "false"

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    execute_headers = {
        **requester_browser,
        "Idempotency-Key": "action-approval-execute-lifecycle-1",
        "X-Request-ID": "action-approval-execute-audit",
    }
    execute_payload = {"expected_revision": approved["revision"]}
    executed_response = client.post(
        f"/iam/action-approvals/{created['id']}/execute",
        headers=execute_headers,
        json=execute_payload,
    )
    assert executed_response.status_code == 200, executed_response.text
    executed = executed_response.json()
    assert executed["approval"]["stored_status"] == "executed"
    assert executed["approval"]["status"] == "executed"
    assert executed["approval"]["revision"] == 3
    assert executed["approval"]["executed_by_user_id"] == str(seed_users["viewer"].id)
    receipt = executed["receipt"]
    assert receipt["approval_request_id"] == created["id"]
    assert receipt["payload_digest"] == created["payload_digest"]
    assert receipt["requester_email"] == "viewer@example.com"
    assert receipt["approver_email"] == "admin@example.com"
    assert receipt["executed_by_email"] == "viewer@example.com"
    assert receipt["result"] == {
        "changed": True,
        "new_revision": account["revision"] + 1,
        "revoked_credentials": 0,
    }
    assert executed_response.headers["X-ThreatLens-Mutation-Changed"] == "true"
    assert executed_response.headers["X-Current-Revision"] == "3"

    execute_replay = client.post(
        f"/iam/action-approvals/{created['id']}/execute",
        headers=execute_headers,
        json=execute_payload,
    )
    assert execute_replay.status_code == 200, execute_replay.text
    assert execute_replay.json() == executed
    assert execute_replay.headers["X-ThreatLens-Mutation-Changed"] == "false"

    receipt_response = client.get(
        f"/iam/action-approvals/{created['id']}/receipt",
        headers=requester_browser,
    )
    assert receipt_response.status_code == 200, receipt_response.text
    assert receipt_response.json() == receipt

    target_response = client.get(
        f"/iam/service-accounts/{account['id']}",
        headers=requester_browser,
    )
    assert target_response.status_code == 200, target_response.text
    assert target_response.json()["is_active"] is False
    assert target_response.json()["revision"] == account["revision"] + 1

    client.cookies.clear()
    approval_audits_response = client.get(
        f"/audit-logs?approval_id={created['id']}&page_size=100",
        headers=auth_headers["admin"],
    )
    assert approval_audits_response.status_code == 200, approval_audits_response.text
    approval_audits = approval_audits_response.json()["logs"]
    approval_actions = {entry["action"] for entry in approval_audits}
    assert {
        "approvals.action.request",
        "approvals.action.approve",
        "approvals.action.execute",
        "service_accounts.disable",
    } <= approval_actions

    receipt_audits_response = client.get(
        f"/audit-logs?execution_receipt_id={receipt['id']}&page_size=100",
        headers=auth_headers["admin"],
    )
    assert receipt_audits_response.status_code == 200, receipt_audits_response.text
    receipt_audits = receipt_audits_response.json()["logs"]
    assert {entry["action"] for entry in receipt_audits} == {
        "approvals.action.execute",
        "service_accounts.disable",
    }
    assert all(
        entry["authorization_approval_id"] == created["id"] for entry in receipt_audits
    )
    assert all(
        entry["execution_receipt_id"] == receipt["id"] for entry in receipt_audits
    )

    db_session.expire_all()
    approval_id = uuid.UUID(created["id"])
    receipt_id = uuid.UUID(receipt["id"])
    stored_receipt = db_session.scalar(
        select(ActionExecutionReceipt).where(
            ActionExecutionReceipt.approval_request_id == approval_id
        )
    )
    assert stored_receipt is not None
    assert stored_receipt.id == receipt_id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ActionExecutionReceipt)
            .where(ActionExecutionReceipt.approval_request_id == approval_id)
        )
        == 1
    )
    execute_audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.request_id == "action-approval-execute-audit",
            AuditLog.action.in_(
                ["approvals.action.execute", "service_accounts.disable"]
            ),
        )
    ).all()
    assert len(execute_audits) == 2
    assert all(
        entry.authorization_approval_id == approval_id for entry in execute_audits
    )
    assert all(entry.execution_receipt_id == receipt_id for entry in execute_audits)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(GovernanceOperationReceipt)
            .where(
                GovernanceOperationReceipt.operation == "action_approval.execute",
                GovernanceOperationReceipt.resource_id == approval_id,
            )
        )
        == 1
    )


def test_generation_zero_approver_security_snapshot_remains_valid(
    client,
    auth_headers,
    seed_users,
):
    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    account = _create_service_account(client, auth_headers["admin"])
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    approval = _create_approval(
        client,
        requester_browser,
        account,
        key="action-approval-generation-zero-create-1",
    )

    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved = _approve(
        client,
        approver_browser,
        approval,
        key="action-approval-generation-zero-decision-1",
    )
    assert approved["decided_auth_token_version"] == 0

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    executed = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers={
            **requester_browser,
            "Idempotency-Key": "action-approval-generation-zero-execute-1",
        },
        json={"expected_revision": approved["revision"]},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["approval"]["stored_status"] == "executed"


def test_action_approval_rejects_self_approval_and_keeps_request_pending(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    account = _create_service_account(client, auth_headers["admin"])
    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approval = _create_approval(
        client,
        admin_browser,
        account,
        key="action-approval-self-create-1",
    )

    denied = client.post(
        f"/iam/action-approvals/{approval['id']}/decision",
        headers={
            **admin_browser,
            "Idempotency-Key": "action-approval-self-decision-1",
            "X-Request-ID": "action-approval-self-decision-audit",
        },
        json={
            "expected_revision": approval["revision"],
            "approve": True,
            "reason": "Attempt to approve my own request.",
        },
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "action_approval_forbidden"
    assert denied.headers["X-ThreatLens-Mutation-Changed"] == "false"

    stored = client.get(
        f"/iam/action-approvals/{approval['id']}",
        headers=admin_browser,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["stored_status"] == "pending"
    assert stored.json()["revision"] == 1
    assert stored.json()["decided_by_user_id"] is None

    db_session.expire_all()
    rejection = db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == "action-approval-self-decision-audit",
            AuditLog.action == "approvals.action.approve",
            AuditLog.success.is_(False),
        )
    )
    assert rejection is not None
    assert rejection.actor_user_id == seed_users["admin"].id
    assert rejection.metadata_json["reason"] == "action_approval_forbidden"


def test_pat_cannot_request_approval_but_direct_disable_remains_available(
    client,
    auth_headers,
    db_session,
):
    account = _create_service_account(client, auth_headers["admin"])
    request_id = "action-approval-pat-rejection-audit"
    rejected = client.post(
        "/iam/action-approvals",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "action-approval-pat-create-1",
            "X-Request-ID": request_id,
        },
        json=_request_payload(account),
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error"]["code"] == "browser_session_required"

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ActionApprovalRequest)
            .where(ActionApprovalRequest.target_id == account["id"])
        )
        == 0
    )

    direct = client.post(
        f"/iam/service-accounts/{account['id']}/disable",
        headers={**auth_headers["admin"], "X-Request-ID": "direct-disable-audit"},
        json={"expected_revision": account["revision"]},
    )
    assert direct.status_code == 200, direct.text
    assert direct.json()["is_active"] is False
    assert direct.json()["revision"] == account["revision"] + 1
    assert direct.headers["X-ThreatLens-Mutation-Changed"] == "true"

    db_session.expire_all()
    target = db_session.get(ServiceAccount, uuid.UUID(account["id"]))
    assert target is not None
    assert target.is_active is False
    direct_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.request_id == "direct-disable-audit",
            AuditLog.action == "service_accounts.disable",
        )
    )
    assert direct_audit is not None
    assert direct_audit.authorization_approval_id is None
    assert direct_audit.execution_receipt_id is None


def test_target_drift_durably_invalidates_and_replays_the_execute_conflict(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    account = _create_service_account(client, auth_headers["admin"])

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    approval = _create_approval(
        client,
        requester_browser,
        account,
        key="action-approval-drift-create-1",
    )
    # Keep this path independent from the generation-zero regression exercised by
    # the full lifecycle test above so target invalidation is reached and verified.
    _set_auth_token_version(db_session, seed_users["admin"], 1)
    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved = _approve(
        client,
        approver_browser,
        approval,
        key="action-approval-drift-decision-1",
    )

    client.cookies.clear()
    drifted = client.patch(
        f"/iam/service-accounts/{account['id']}",
        headers=auth_headers["admin"],
        json={
            "expected_revision": account["revision"],
            "name": "Changed after approval",
        },
    )
    assert drifted.status_code == 200, drifted.text
    assert drifted.json()["revision"] == account["revision"] + 1
    assert drifted.json()["is_active"] is True

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    execute_headers = {
        **requester_browser,
        "Idempotency-Key": "action-approval-drift-execute-1",
        "X-Request-ID": "action-approval-drift-execute-audit",
    }
    execute_payload = {"expected_revision": approved["revision"]}
    invalidated = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers=execute_headers,
        json=execute_payload,
    )
    assert invalidated.status_code == 409, invalidated.text
    assert invalidated.json()["error"]["code"] == "action_approval_invalidated"
    assert invalidated.json()["error"]["context"] == {
        "current_revision": 3,
        "invalidation_reason": "target_preconditions_changed",
    }
    assert invalidated.headers["X-ThreatLens-Mutation-Changed"] == "true"
    assert invalidated.headers["X-Current-Revision"] == "3"

    replay = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers=execute_headers,
        json=execute_payload,
    )
    assert replay.status_code == 409, replay.text
    assert replay.json() == invalidated.json()
    assert replay.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert replay.headers["X-Current-Revision"] == "3"

    stored_response = client.get(
        f"/iam/action-approvals/{approval['id']}",
        headers=requester_browser,
    )
    assert stored_response.status_code == 200, stored_response.text
    stored_payload = stored_response.json()
    assert stored_payload["stored_status"] == "invalidated"
    assert stored_payload["status"] == "invalidated"
    assert stored_payload["revision"] == 3
    assert stored_payload["invalidation_reason"] == "target_preconditions_changed"
    assert stored_payload["invalidated_at"] is not None
    assert stored_payload["executed_at"] is None

    target_response = client.get(
        f"/iam/service-accounts/{account['id']}",
        headers=requester_browser,
    )
    assert target_response.status_code == 200, target_response.text
    assert target_response.json()["is_active"] is True
    assert target_response.json()["revision"] == account["revision"] + 1

    db_session.expire_all()
    approval_id = uuid.UUID(approval["id"])
    stored = db_session.get(ActionApprovalRequest, approval_id)
    assert stored is not None
    assert stored.status == "invalidated"
    assert stored.revision == 3
    assert stored.invalidation_reason == "target_preconditions_changed"
    assert stored.executed_at is None
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ActionExecutionReceipt)
            .where(ActionExecutionReceipt.approval_request_id == approval_id)
        )
        == 0
    )
    replay_receipts = db_session.scalars(
        select(GovernanceOperationReceipt).where(
            GovernanceOperationReceipt.operation == "action_approval.execute",
            GovernanceOperationReceipt.resource_id == approval_id,
        )
    ).all()
    assert len(replay_receipts) == 1
    assert replay_receipts[0].http_status == 409
    assert replay_receipts[0].response_json["error_code"] == (
        "action_approval_invalidated"
    )
    invalidation_audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.request_id == "action-approval-drift-execute-audit",
            AuditLog.action == "approvals.action.invalidate",
        )
    ).all()
    assert len(invalidation_audits) == 1
    assert invalidation_audits[0].authorization_approval_id == approval_id
    target_failure_audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.request_id == "action-approval-drift-execute-audit",
            AuditLog.action == "service_accounts.disable",
            AuditLog.success.is_(False),
        )
    ).all()
    assert len(target_failure_audits) == 1
    assert target_failure_audits[0].authorization_approval_id == approval_id
    assert target_failure_audits[0].execution_receipt_id is None


def test_deleted_target_durably_invalidates_instead_of_repeating_404(
    client,
    auth_headers,
    seed_users,
):
    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    account = _create_service_account(client, auth_headers["admin"])
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    approval = _create_approval(
        client,
        requester_browser,
        account,
        key="action-approval-missing-create-1",
    )
    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved = _approve(
        client,
        approver_browser,
        approval,
        key="action-approval-missing-decision-1",
    )

    client.cookies.clear()
    disabled = client.post(
        f"/iam/service-accounts/{account['id']}/disable",
        headers=auth_headers["admin"],
        json={"expected_revision": account["revision"]},
    )
    assert disabled.status_code == 200, disabled.text
    deleted = client.delete(
        f"/iam/service-accounts/{account['id']}?expected_revision={disabled.json()['revision']}",
        headers=auth_headers["admin"],
    )
    assert deleted.status_code == 204, deleted.text

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    headers = {
        **requester_browser,
        "Idempotency-Key": "action-approval-missing-execute-1",
    }
    payload = {"expected_revision": approved["revision"]}
    invalidated = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers=headers,
        json=payload,
    )
    assert invalidated.status_code == 409, invalidated.text
    assert invalidated.json()["error"]["code"] == "action_approval_invalidated"
    assert invalidated.json()["error"]["context"]["invalidation_reason"] == (
        "target_missing"
    )
    replay = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers=headers,
        json=payload,
    )
    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"] == invalidated.json()["detail"]
    assert replay.json()["error"]["code"] == "action_approval_invalidated"
    assert replay.json()["error"]["context"] == invalidated.json()["error"]["context"]
    assert replay.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert replay.headers["X-Current-Revision"] == "3"


def test_create_replay_rechecks_action_specific_durable_permission(
    client,
    auth_headers,
    seed_users,
):
    approvals_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:approvals", "write:approvals"],
    )
    domain_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:service_accounts"],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=approvals_role,
    )
    domain_assignment = _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=domain_role,
    )
    account = _create_service_account(client, auth_headers["admin"])
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    headers = {
        **requester_browser,
        "Idempotency-Key": "action-approval-replay-domain-create-1",
    }
    payload = _request_payload(account)
    created = client.post(
        "/iam/action-approvals",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 201, created.text

    client.cookies.clear()
    removed = client.delete(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments/{domain_assignment['id']}",
        headers=auth_headers["admin"],
    )
    assert removed.status_code == 204, removed.text
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    replay = client.post(
        "/iam/action-approvals",
        headers={
            **requester_browser,
            "Idempotency-Key": "action-approval-replay-domain-create-1",
        },
        json=payload,
    )
    assert replay.status_code == 403, replay.text
    assert replay.json()["error"]["code"] == "action_approval_forbidden"
    assert "target_snapshot" not in replay.text


def test_approver_security_generation_drift_blocks_execution(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester_access(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
    )
    account = _create_service_account(client, auth_headers["admin"])
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    approval = _create_approval(
        client,
        requester_browser,
        account,
        key="action-approval-generation-drift-create-1",
    )
    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved = _approve(
        client,
        approver_browser,
        approval,
        key="action-approval-generation-drift-decision-1",
    )
    _set_auth_token_version(
        db_session,
        seed_users["admin"],
        approved["decided_auth_token_version"] + 1,
    )

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    rejected = client.post(
        f"/iam/action-approvals/{approval['id']}/execute",
        headers={
            **requester_browser,
            "Idempotency-Key": "action-approval-generation-drift-execute-1",
        },
        json={"expected_revision": approved["revision"]},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "action_approval_conflict"
    current = client.get(
        f"/iam/action-approvals/{approval['id']}",
        headers=requester_browser,
    )
    assert current.status_code == 200, current.text
    assert current.json()["stored_status"] == "approved"
    assert current.json()["revision"] == approved["revision"]


def test_custom_role_delete_executes_through_registered_handler(
    client,
    auth_headers,
    seed_users,
):
    requester_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=[
            "read:approvals",
            "write:approvals",
            "read:iam",
        ],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=requester_role,
    )
    target_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:feeds"],
    )
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    created = client.post(
        "/iam/action-approvals",
        headers={
            **requester_browser,
            "Idempotency-Key": "action-approval-role-delete-create-1",
        },
        json={
            "action_type": "iam.role.delete",
            "target_id": target_role["id"],
            "target_revision": target_role["revision"],
            "payload": {},
            "expires_in_seconds": 3_600,
            "reason": "Remove an obsolete unassigned custom role.",
        },
    )
    assert created.status_code == 201, created.text
    approver_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approved = _approve(
        client,
        approver_browser,
        created.json(),
        key="action-approval-role-delete-decision-1",
    )
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    executed = client.post(
        f"/iam/action-approvals/{created.json()['id']}/execute",
        headers={
            **requester_browser,
            "Idempotency-Key": "action-approval-role-delete-execute-1",
        },
        json={"expected_revision": approved["revision"]},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["receipt"]["result"] == {
        "changed": True,
        "deleted_role_key": target_role["key"],
        "deleted_role_name": target_role["name"],
    }
    client.cookies.clear()
    roles = client.get("/iam/roles", headers=auth_headers["admin"])
    assert roles.status_code == 200, roles.text
    assert target_role["id"] not in {role["id"] for role in roles.json()}
