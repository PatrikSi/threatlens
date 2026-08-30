from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.access_review import (
    AccessReviewApplyReceipt,
    AccessReviewDecision,
    AccessReviewItem,
)
from app.models.audit_log import AuditLog
from app.models.iam import IAMUserRoleAssignment
from app.models.user import User
from app.services.access_review_apply import (
    AccessReviewMutationResult,
    apply_access_review_item,
)
from app.services.access_review_mutations import AccessReviewMutationBlocked


def _browser_login(client, *, email: str, password: str) -> dict[str, str]:
    client.cookies.clear()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf_token = response.json().get("csrf_token")
    assert csrf_token
    return {"X-CSRF-Token": csrf_token}


def _create_role(client, admin_headers, *, permissions: list[str]) -> dict:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/iam/roles",
        headers=admin_headers,
        json={
            "key": f"access-review-{suffix}",
            "name": f"Access review {suffix}",
            "description": "Access-review integration-test role.",
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


def _create_campaign(
    client,
    browser_headers,
    *,
    user_ids: list[uuid.UUID],
    key: str,
) -> dict:
    response = client.post(
        "/iam/access-reviews",
        headers={**browser_headers, "Idempotency-Key": key},
        json={
            "name": "Quarterly privileged access review",
            "description": "Validate durable and compatibility access.",
            "user_ids": [str(value) for value in user_ids],
            "service_account_ids": [],
            "include_oidc_mappings": False,
            "include_live_elevations": False,
            "due_in_seconds": 86_400,
        },
    )
    assert response.status_code == 201, response.text
    assert response.headers["X-ThreatLens-Mutation-Changed"] == "true"
    return response.json()


def _items(client, browser_headers, campaign_id: str) -> list[dict]:
    response = client.get(
        f"/iam/access-reviews/{campaign_id}/items?page_size=100",
        headers=browser_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _record_all_decisions(
    client,
    browser_headers,
    campaign: dict,
    items: list[dict],
    *,
    revoke_types: set[str],
    key: str,
) -> dict:
    response = client.post(
        f"/iam/access-reviews/{campaign['id']}/decisions",
        headers={**browser_headers, "Idempotency-Key": key},
        json={
            "expected_revision": campaign["revision"],
            "decisions": [
                {
                    "item_id": item["id"],
                    "decision": (
                        "revoke" if item["item_type"] in revoke_types else "retain"
                    ),
                    "reason": "Reviewed against current operating responsibilities.",
                }
                for item in items
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _transition(
    client,
    browser_headers,
    campaign: dict,
    *,
    path: str,
    key: str,
    reason: str | None = None,
) -> dict:
    payload: dict[str, object] = {"expected_revision": campaign["revision"]}
    if reason is not None:
        payload["reason"] = reason
    response = client.post(
        f"/iam/access-reviews/{campaign['id']}/{path}",
        headers={**browser_headers, "Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_access_review_lifecycle_revokes_local_access_and_replays_after_completion(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    audit_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:audit"],
    )
    assignment = _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=audit_role,
    )
    assert client.get("/audit-logs", headers=auth_headers["viewer"]).status_code == 200

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    campaign = _create_campaign(
        client,
        admin_browser,
        user_ids=[seed_users["viewer"].id],
        key="access-review-lifecycle-create-1",
    )
    items = _items(client, admin_browser, campaign["id"])
    assert {item["item_type"] for item in items} == {
        "legacy_user_role",
        "direct_user_role",
    }
    campaign = _record_all_decisions(
        client,
        admin_browser,
        campaign,
        items,
        revoke_types={"direct_user_role"},
        key="access-review-lifecycle-decisions-1",
    )
    assert campaign["decided_item_count"] == 2
    assert campaign["revoke_item_count"] == 1
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="close",
        key="access-review-lifecycle-close-1",
        reason="All assignment decisions are complete.",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/start",
        key="access-review-lifecycle-start-1",
    )
    assert campaign["status"] == "applying"

    receipts: dict[str, dict] = {}
    for item in items:
        key = f"access-review-lifecycle-apply-{item['item_type']}"
        response = client.post(
            f"/iam/access-reviews/{campaign['id']}/apply/items/{item['id']}",
            headers={**admin_browser, "Idempotency-Key": key},
            json={
                "expected_revision": campaign["revision"],
                "expected_item_fingerprint": item["assignment_fingerprint"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["X-ThreatLens-Mutation-Changed"] == "true"
        receipts[item["item_type"]] = response.json()

    assert receipts["legacy_user_role"]["outcome"] == "retained"
    revoke_receipt = receipts["direct_user_role"]
    assert revoke_receipt["outcome"] == "revoked"
    assert revoke_receipt["mutation_performed"] is True
    assert revoke_receipt["result_snapshot"]["revoked_api_tokens"] >= 1
    assert db_session.get(IAMUserRoleAssignment, uuid.UUID(assignment["id"])) is None

    completed = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/complete",
        key="access-review-lifecycle-complete-1",
    )
    assert completed["status"] == "applied"
    assert completed["apply_terminal_item_count"] == 2

    direct_item = next(
        item for item in items if item["item_type"] == "direct_user_role"
    )
    replay = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{direct_item['id']}",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-lifecycle-apply-direct_user_role",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": direct_item["assignment_fingerprint"],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == revoke_receipt["id"]
    assert replay.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert replay.headers["X-Current-Revision"] == str(completed["revision"])

    terminal_retry = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{direct_item['id']}",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-lifecycle-terminal-retry-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": direct_item["assignment_fingerprint"],
        },
    )
    assert terminal_retry.status_code == 200, terminal_retry.text
    assert terminal_retry.json()["id"] == revoke_receipt["id"]
    assert terminal_retry.headers["X-ThreatLens-Mutation-Changed"] == "false"
    assert terminal_retry.headers["X-Current-Revision"] == str(completed["revision"])

    wrong_fingerprint = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{direct_item['id']}",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-lifecycle-wrong-fingerprint-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": "0" * 64,
        },
    )
    assert wrong_fingerprint.status_code == 409, wrong_fingerprint.text
    assert client.get("/audit-logs", headers=auth_headers["viewer"]).status_code == 401

    db_session.expire_all()
    audit_actions = set(
        db_session.scalars(
            select(AuditLog.action).where(
                AuditLog.resource_id.in_([campaign["id"], str(seed_users["viewer"].id)])
            )
        ).all()
    )
    assert {
        "access_reviews.campaign.create",
        "access_reviews.decisions.record",
        "access_reviews.apply.start",
        "access_reviews.item.apply",
        "access_reviews.apply.complete",
        "iam.user_role.remove",
    } <= audit_actions
    role_removal_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "iam.user_role.remove",
            AuditLog.resource_id == str(seed_users["viewer"].id),
        )
    )
    assert role_removal_audit is not None
    assert role_removal_audit.resource_type == "user"
    assert role_removal_audit.metadata_json["assignment_id"] == assignment["id"]
    assert role_removal_audit.metadata_json["role_id"] == audit_role["id"]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "iam.user_role.remove",
                AuditLog.resource_id == str(seed_users["viewer"].id),
            )
        )
        == 1
    )


def test_manual_base_role_revoke_requires_resolution_before_completion(
    client,
    seed_users,
):
    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    campaign = _create_campaign(
        client,
        admin_browser,
        user_ids=[seed_users["viewer"].id],
        key="access-review-manual-create-1",
    )
    items = _items(client, admin_browser, campaign["id"])
    assert len(items) == 1
    item = items[0]
    assert item["item_type"] == "legacy_user_role"
    campaign = _record_all_decisions(
        client,
        admin_browser,
        campaign,
        items,
        revoke_types={"legacy_user_role"},
        key="access-review-manual-decisions-1",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="close",
        key="access-review-manual-close-1",
        reason="The base role requires explicit remediation.",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/start",
        key="access-review-manual-start-1",
    )
    applied = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{item['id']}",
        headers={**admin_browser, "Idempotency-Key": "access-review-manual-apply-1"},
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": item["assignment_fingerprint"],
        },
    )
    assert applied.status_code == 200, applied.text
    manual_receipt = applied.json()
    assert manual_receipt["outcome"] == "manual_action_required"
    assert manual_receipt["mutation_performed"] is False

    incomplete = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/complete",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-manual-premature-complete-1",
        },
        json={"expected_revision": campaign["revision"]},
    )
    assert incomplete.status_code == 409, incomplete.text
    assert incomplete.json()["error"]["code"] == "access_review_incomplete"

    resolved = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{item['id']}/resolve",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-manual-resolve-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": item["assignment_fingerprint"],
            "expected_receipt_attempt": manual_receipt["attempt"],
            "reason": "The base-role exception is documented and approved for this cycle.",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["outcome"] == "superseded"
    completed = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/complete",
        key="access-review-manual-complete-1",
    )
    assert completed["status"] == "applied"
    assert seed_users["viewer"].role == "viewer"


def test_mutations_require_browser_and_self_review_is_rejected_atomically(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    reviewer_role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:access_reviews", "write:access_reviews"],
    )
    _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=reviewer_role,
    )
    token_attempt = client.post(
        "/iam/access-reviews",
        headers={
            **auth_headers["viewer"],
            "Idempotency-Key": "access-review-token-create-1",
        },
        json={
            "name": "Token-created review",
            "user_ids": [str(seed_users["analyst"].id)],
            "include_oidc_mappings": False,
            "include_live_elevations": False,
        },
    )
    assert token_attempt.status_code == 403, token_attempt.text
    assert token_attempt.json()["error"]["code"] == "browser_session_required"

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    campaign = _create_campaign(
        client,
        admin_browser,
        user_ids=[seed_users["viewer"].id, seed_users["analyst"].id],
        key="access-review-self-create-1",
    )
    items = _items(client, admin_browser, campaign["id"])
    self_item = next(
        item
        for item in items
        if item["principal_id_snapshot"] == str(seed_users["viewer"].id)
    )
    viewer_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    rejected = client.post(
        f"/iam/access-reviews/{campaign['id']}/decisions",
        headers={
            **viewer_browser,
            "Idempotency-Key": "access-review-self-decision-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "decisions": [
                {
                    "item_id": self_item["id"],
                    "decision": "retain",
                    "reason": "Attempting to retain my own access.",
                }
            ],
        },
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error"]["code"] == "access_review_forbidden"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AccessReviewDecision)
            .where(AccessReviewDecision.campaign_id == uuid.UUID(campaign["id"]))
        )
        == 0
    )

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    campaign = _record_all_decisions(
        client,
        admin_browser,
        campaign,
        items,
        revoke_types={"legacy_user_role"},
        key="access-review-self-admin-decisions-1",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="close",
        key="access-review-self-close-1",
        reason="Independent decisions are complete.",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/start",
        key="access-review-self-start-1",
    )
    viewer_legacy_item = next(
        item
        for item in items
        if item["principal_id_snapshot"] == str(seed_users["viewer"].id)
        and item["item_type"] == "legacy_user_role"
    )
    applied = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{viewer_legacy_item['id']}",
        headers={
            **admin_browser,
            "Idempotency-Key": "access-review-self-admin-apply-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": viewer_legacy_item["assignment_fingerprint"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["outcome"] == "manual_action_required"

    viewer_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    self_resolution = client.post(
        f"/iam/access-reviews/{campaign['id']}/apply/items/{viewer_legacy_item['id']}/resolve",
        headers={
            **viewer_browser,
            "Idempotency-Key": "access-review-self-resolution-1",
        },
        json={
            "expected_revision": campaign["revision"],
            "expected_item_fingerprint": viewer_legacy_item["assignment_fingerprint"],
            "expected_receipt_attempt": applied.json()["attempt"],
            "reason": "Attempting to resolve my own access exception.",
        },
    )
    assert self_resolution.status_code == 403, self_resolution.text
    assert self_resolution.json()["error"]["code"] == "access_review_forbidden"


def test_inconsistent_coordinator_rolls_back_its_savepoint_before_failed_receipt(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    role = _create_role(
        client,
        auth_headers["admin"],
        permissions=["read:audit"],
    )
    assignment = _assign_role(
        client,
        auth_headers["admin"],
        user_id=seed_users["viewer"].id,
        role=role,
    )
    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    campaign = _create_campaign(
        client,
        admin_browser,
        user_ids=[seed_users["viewer"].id],
        key="access-review-savepoint-create-1",
    )
    items = _items(client, admin_browser, campaign["id"])
    campaign = _record_all_decisions(
        client,
        admin_browser,
        campaign,
        items,
        revoke_types={"direct_user_role"},
        key="access-review-savepoint-decisions-1",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="close",
        key="access-review-savepoint-close-1",
        reason="Ready to exercise coordinator rollback.",
    )
    campaign = _transition(
        client,
        admin_browser,
        campaign,
        path="apply/start",
        key="access-review-savepoint-start-1",
    )
    direct_item_payload = next(
        item for item in items if item["item_type"] == "direct_user_role"
    )
    direct_item = db_session.get(AccessReviewItem, uuid.UUID(direct_item_payload["id"]))
    actor = db_session.get(User, seed_users["admin"].id)
    assert direct_item is not None and actor is not None

    def inconsistent_coordinator(session, context):
        assignment_row = session.get(
            IAMUserRoleAssignment, context.current_assignment.assignment_id
        )
        target = session.get(User, context.item.principal_id_snapshot)
        assert assignment_row is not None and target is not None
        session.delete(assignment_row)
        target.is_approved = False
        session.add(target)
        return AccessReviewMutationResult(mutation_performed=False)

    result = apply_access_review_item(
        db_session,
        campaign_id=uuid.UUID(campaign["id"]),
        item_id=direct_item.id,
        actor=actor,
        expected_revision=campaign["revision"],
        expected_item_fingerprint=direct_item.assignment_fingerprint,
        coordinator=inconsistent_coordinator,
    )
    assert result.changed is True
    assert result.receipt.outcome == "failed"
    db_session.commit()
    db_session.expire_all()
    assert (
        db_session.get(IAMUserRoleAssignment, uuid.UUID(assignment["id"])) is not None
    )
    assert db_session.get(User, seed_users["viewer"].id).is_approved is True
    stored_receipt = db_session.get(AccessReviewApplyReceipt, result.receipt.id)
    assert stored_receipt is not None
    assert stored_receipt.mutation_performed is False
    assert stored_receipt.detail_code == "coordinator_result_inconsistent"

    direct_item = db_session.get(AccessReviewItem, direct_item.id)
    actor = db_session.get(User, seed_users["admin"].id)
    assert direct_item is not None and actor is not None

    def blocked_coordinator(session, context):
        assignment_row = session.get(
            IAMUserRoleAssignment, context.current_assignment.assignment_id
        )
        assert assignment_row is not None
        session.delete(assignment_row)
        raise AccessReviewMutationBlocked(
            "The dependent ownership invariant blocked this access reduction.",
            context={
                "reason": "investigation_owner_reassignment_required",
                "affected_investigation_count": 2,
                "affected_investigation_ids": ["must-not-leak"],
            },
        )

    blocked = apply_access_review_item(
        db_session,
        campaign_id=uuid.UUID(campaign["id"]),
        item_id=direct_item.id,
        actor=actor,
        expected_revision=campaign["revision"],
        expected_item_fingerprint=direct_item.assignment_fingerprint,
        coordinator=blocked_coordinator,
    )
    assert blocked.receipt.outcome == "failed"
    assert blocked.receipt.attempt == 2
    assert blocked.receipt.detail_code == "access_review_mutation_blocked"
    assert blocked.receipt.result_snapshot == {
        "error_code": "access_review_mutation_blocked",
        "error_context": {
            "reason": "investigation_owner_reassignment_required",
            "affected_investigation_count": 2,
        },
    }
    db_session.commit()
    db_session.expire_all()
    assert (
        db_session.get(IAMUserRoleAssignment, uuid.UUID(assignment["id"])) is not None
    )
