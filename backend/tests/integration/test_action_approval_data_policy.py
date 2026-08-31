from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import func, select

from app.api.deps import get_data_access_context
from app.core.config import get_settings
from app.main import app
from app.models.action_approval import (
    ActionApprovalRequest,
    ActionExecutionReceipt,
)
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.data_policy import (
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_ops import (
    AI_TASK_TYPE_CONNECTION_TEST,
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    AI_TRIGGER_MANUAL,
    queue_ai_task_run,
)
from app.services import action_approvals as action_approval_service
from app.services.action_approval_data_policy import (
    action_approval_data_policy_blocker_count,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DataAccessSourceInput,
    get_data_access_envelope,
    merge_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyRevisionConflict,
    assign_feed_handling_label,
)
from app.services.history_maintenance import prune_application_history


def _create_role(client, admin_headers, *, permissions: list[str]) -> dict:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/iam/roles",
        headers=admin_headers,
        json={
            "key": f"approval-policy-{suffix}",
            "name": f"Approval policy {suffix}",
            "description": "Narrow action-approval data-policy test role.",
            "permissions": permissions,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assign_role(client, admin_headers, *, user_id: uuid.UUID, role: dict) -> None:
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
    client.cookies.clear()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _context(
    db_session,
    *,
    principal_id: uuid.UUID,
    allowed_label_ids: set[uuid.UUID],
    mode: str = "enforced",
) -> DataAccessContext:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    return DataAccessContext(
        mode=mode,
        policy_revision=state.revision,
        coverage_version=1 if mode != "disabled" else 0,
        principal_type="user",
        principal_id=principal_id,
        principal_eligible=True,
        allowed_label_ids=frozenset(
            {UNRESTRICTED_HANDLING_LABEL_ID, *allowed_label_ids}
        ),
    )


@contextmanager
def _override_data_access(context: DataAccessContext) -> Iterator[None]:
    previous = app.dependency_overrides.get(get_data_access_context)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous


def _restricted_receipt(db_session, *, actor_user_id: uuid.UUID):
    suffix = uuid.uuid4().hex
    label = HandlingLabel(
        key=f"approval-restricted-{suffix[:12]}",
        name=f"Approval restricted {suffix[:8]}",
        description="Restricted approval target.",
        color="#991B1B",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
    )
    db_session.add(label)
    db_session.flush()
    feed = Feed(
        name=f"Approval feed {suffix[:8]}",
        url=f"https://approval-{suffix}.example/feed.xml",
        handling_label_id=label.id,
    )
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        source_guid=f"approval-{suffix}",
        url=f"https://approval-{suffix}.example/item",
        canonical_url=f"https://approval-{suffix}.example/item",
        title="Restricted approval item",
        dedupe_key=f"approval:{suffix}",
        content_hash=suffix.ljust(64, "0")[:64],
        status="content_fetched",
    )
    db_session.add(item)
    db_session.flush()
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor_user_id,
        item_id=item.id,
    )
    receipt = AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(),
        attempt_number=1,
        request_fingerprint=suffix.ljust(64, "a")[:64],
        task_run_id_snapshot=run.id,
        feature_type="item_enrichment",
        resource_type="item",
        resource_id=item.id,
        max_attempts=3,
        requested_max_tokens=1_024,
        iam_revision=1,
        data_policy_revision=1,
        data_policy_mode="enforced",
        state="ambiguous",
        io_outcome="ambiguous",
        retryable=False,
        settled_at=func.now(),
    )
    db_session.add(receipt)
    db_session.commit()
    return label, feed, run, receipt


def _system_receipt(db_session, *, actor_user_id: uuid.UUID):
    suffix = uuid.uuid4().hex
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_CONNECTION_TEST,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=actor_user_id,
    )
    receipt = AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(),
        attempt_number=1,
        request_fingerprint=suffix.ljust(64, "c")[:64],
        task_run_id_snapshot=run.id,
        feature_type=AI_TASK_TYPE_CONNECTION_TEST,
        resource_type="ai_settings",
        resource_id=None,
        max_attempts=1,
        requested_max_tokens=512,
        iam_revision=1,
        data_policy_revision=1,
        data_policy_mode="enforced",
        state="ambiguous",
        io_outcome="ambiguous",
        retryable=False,
        settled_at=func.now(),
    )
    db_session.add(receipt)
    db_session.commit()
    return run, receipt


def _grant_requester(client, admin_headers, user_id: uuid.UUID) -> None:
    role = _create_role(
        client,
        admin_headers,
        permissions=[
            "read:approvals",
            "write:approvals",
            "read:ai",
            "read:service_accounts",
        ],
    )
    _assign_role(client, admin_headers, user_id=user_id, role=role)


def _create_service_account(client, admin_headers) -> dict:
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/iam/service-accounts",
        headers=admin_headers,
        json={
            "key": f"approval-policy-{suffix}",
            "name": f"Approval policy {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post_approval(
    client,
    browser_headers,
    *,
    key: str,
    action_type: str,
    target_id: str,
    target_revision: int,
):
    return client.post(
        "/iam/action-approvals",
        headers={**browser_headers, "Idempotency-Key": key},
        json={
            "action_type": action_type,
            "target_id": target_id,
            "target_revision": target_revision,
            "payload": {},
            "expires_in_seconds": 3_600,
            "reason": "Perform this sensitive action after independent review.",
        },
    )


def test_access_predicate_filters_totals_pagination_detail_and_receipt(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    accounts = [
        _create_service_account(client, auth_headers["admin"]),
        _create_service_account(client, auth_headers["admin"]),
    ]
    label, _feed, run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    allowed = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id},
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.coverage_version = 1
    db_session.add(state)
    db_session.commit()
    with _override_data_access(allowed):
        system_approvals = []
        for index, account in enumerate(accounts):
            response = _post_approval(
                client,
                browser,
                key=f"approval-policy-system-{index}",
                action_type="service_account.disable",
                target_id=account["id"],
                target_revision=account["revision"],
            )
            assert response.status_code == 201, response.text
            system_approvals.append(response.json())
        governed_response = _post_approval(
            client,
            browser,
            key="approval-policy-governed",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
        assert governed_response.status_code == 201, governed_response.text
    governed = governed_response.json()

    stored = db_session.get(ActionApprovalRequest, uuid.UUID(governed["id"]))
    assert stored is not None
    assert stored.data_access_scope == "governed"
    assert stored.data_access_source_type == "ai_task_run"
    assert stored.data_access_source_id == run.id
    envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=stored.id,
    )
    assert envelope is not None
    assert envelope.label_ids == {label.id}
    stored.status = "executed"
    stored.decided_by_user_id = seed_users["admin"].id
    stored.decided_by_email_snapshot = seed_users["admin"].email
    stored.decided_at = stored.created_at + timedelta(minutes=1)
    stored.decision_reason = "Independently reviewed execution receipt."
    stored.decided_auth_token_version_snapshot = int(
        seed_users["admin"].auth_token_version or 0
    )
    stored.decided_auth_method_snapshot = "local"
    stored.executed_by_user_id = stored.requested_by_user_id
    stored.executed_by_email_snapshot = stored.requested_by_email_snapshot
    stored.executed_at = stored.created_at + timedelta(minutes=2)
    stored.revision = 3
    stored.updated_at = stored.executed_at
    db_session.add(stored)
    db_session.add(
        ActionExecutionReceipt(
            approval_request_id=stored.id,
            action_type=stored.action_type,
            target_type=stored.target_type,
            target_id=stored.target_id,
            target_revision=stored.target_revision,
            payload_digest=stored.payload_digest,
            requester_user_id=stored.requested_by_user_id,
            requester_email_snapshot=stored.requested_by_email_snapshot,
            approver_user_id=seed_users["admin"].id,
            approver_email_snapshot=seed_users["admin"].email,
            executed_by_user_id=stored.requested_by_user_id,
            executed_by_email_snapshot=stored.requested_by_email_snapshot,
            result_json={"private_result": "restricted"},
        )
    )
    db_session.commit()

    system_run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_CONNECTION_TEST,
        trigger_source=AI_TRIGGER_MANUAL,
        actor_user_id=seed_users["admin"].id,
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.coverage_version = 0
    db_session.add(state)
    db_session.commit()
    borrowed_system_scope = ActionApprovalRequest(
        id=uuid.uuid4(),
        action_type=stored.action_type,
        action_label_snapshot=stored.action_label_snapshot,
        audit_action_snapshot=stored.audit_action_snapshot,
        requester_permission_snapshot=stored.requester_permission_snapshot,
        approver_permission_snapshot=stored.approver_permission_snapshot,
        action_definition_version=1,
        target_data_policy_version=1,
        data_access_scope="system",
        data_access_lineage_complete=True,
        data_access_source_type="ai_task_run",
        data_access_source_id=system_run.id,
        target_type=stored.target_type,
        target_id=stored.target_id,
        target_revision=stored.target_revision,
        target_snapshot=dict(stored.target_snapshot),
        payload_json={},
        payload_digest=stored.payload_digest,
        requested_by_user_id=stored.requested_by_user_id,
        requested_by_email_snapshot=stored.requested_by_email_snapshot,
        request_reason="Attempt to borrow unrelated system target lineage.",
        expires_at=stored.expires_at,
        status="pending",
        revision=1,
        created_at=stored.created_at,
        updated_at=stored.created_at,
    )
    db_session.add(borrowed_system_scope)
    db_session.commit()
    assert action_approval_data_policy_blocker_count(db_session) >= 1
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.coverage_version = 1
    db_session.add(state)
    db_session.commit()

    denied = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids=set(),
    )
    with _override_data_access(denied):
        first_page = client.get(
            "/iam/action-approvals?page=1&page_size=1",
            headers=browser,
        )
        second_page = client.get(
            "/iam/action-approvals?page=2&page_size=1",
            headers=browser,
        )
        hidden_detail = client.get(
            f"/iam/action-approvals/{governed['id']}", headers=browser
        )
        hidden_receipt = client.get(
            f"/iam/action-approvals/{governed['id']}/receipt", headers=browser
        )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert first_page.json()["total"] == 2
    assert second_page.json()["total"] == 2
    served_ids = {
        first_page.json()["approvals"][0]["id"],
        second_page.json()["approvals"][0]["id"],
    }
    assert served_ids == {item["id"] for item in system_approvals}
    assert hidden_detail.status_code == 404
    assert hidden_receipt.status_code == 404
    assert hidden_detail.json()["error"]["code"] == "action_approval_not_found"
    assert hidden_receipt.json()["error"]["code"] == "action_approval_not_found"


def test_create_authorizes_before_preconditions_and_replay(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    label, _feed, _run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    denied = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids=set(),
    )
    missing_id = uuid.uuid4()
    with _override_data_access(denied):
        stale = _post_approval(
            client,
            browser,
            key="approval-policy-stale-hidden",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision + 99,
        )
        missing = _post_approval(
            client,
            browser,
            key="approval-policy-missing-hidden",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(missing_id),
            target_revision=1,
        )
        receipt.state = "succeeded"
        receipt.io_outcome = "response_received"
        receipt.retryable = False
        receipt.revision += 1
        db_session.add(receipt)
        db_session.commit()
        invalid = _post_approval(
            client,
            browser,
            key="approval-policy-invalid-hidden",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )

    assert stale.status_code == missing.status_code == invalid.status_code == 404
    for response in (stale, missing, invalid):
        assert response.json()["detail"] == "Action target not found."
        assert response.json()["error"]["code"] == "action_approval_not_found"

    receipt.state = "ambiguous"
    receipt.io_outcome = "ambiguous"
    receipt.retryable = False
    receipt.revision += 1
    db_session.add(receipt)
    db_session.commit()
    allowed = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(allowed):
        created = _post_approval(
            client,
            browser,
            key="approval-policy-replay-reauth",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert created.status_code == 201, created.text
    with _override_data_access(denied):
        replay = _post_approval(
            client,
            browser,
            key="approval-policy-replay-reauth",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert replay.status_code == 404
    assert "target_snapshot" not in replay.text

    with _override_data_access(allowed):
        cancelled = client.post(
            f"/iam/action-approvals/{created.json()['id']}/cancel",
            headers={
                **browser,
                "Idempotency-Key": "approval-policy-cancel-replay",
            },
            json={
                "expected_revision": created.json()["revision"],
                "reason": "Requester no longer wants this action performed.",
            },
        )
    assert cancelled.status_code == 200, cancelled.text
    with _override_data_access(denied):
        cancel_replay = client.post(
            f"/iam/action-approvals/{created.json()['id']}/cancel",
            headers={
                **browser,
                "Idempotency-Key": "approval-policy-cancel-replay",
            },
            json={
                "expected_revision": created.json()["revision"],
                "reason": "Requester no longer wants this action performed.",
            },
        )
    assert cancel_replay.status_code == 404
    assert "target_snapshot" not in cancel_replay.text


def test_hidden_mutation_matches_missing_before_sensitive_session_check(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    label, _feed, _run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    with _override_data_access(
        _context(
            db_session,
            principal_id=seed_users["viewer"].id,
            allowed_label_ids={label.id},
        )
    ):
        created = _post_approval(
            client,
            requester_browser,
            key="approval-policy-hidden-step-up-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert created.status_code == 201, created.text

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    session_token = client.cookies.get(get_settings().auth_cookie_name)
    assert session_token
    session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash
            == hashlib.sha256(session_token.encode()).hexdigest()
        )
    )
    assert session is not None
    session.authenticated_at = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().auth_recent_auth_seconds + 1
    )
    db_session.add(session)
    db_session.commit()

    denied = _context(
        db_session,
        principal_id=seed_users["admin"].id,
        allowed_label_ids=set(),
    )
    responses = []
    with _override_data_access(denied):
        for key, approval_id in (
            ("approval-policy-hidden-step-up", created.json()["id"]),
            ("approval-policy-missing-step-up", str(uuid.uuid4())),
        ):
            responses.append(
                client.post(
                    f"/iam/action-approvals/{approval_id}/decision",
                    headers={**admin_browser, "Idempotency-Key": key},
                    json={
                        "expected_revision": created.json()["revision"],
                        "approve": True,
                        "reason": "Independently reviewed hidden target.",
                    },
                )
            )

    for response in responses:
        assert response.status_code == 404
        assert response.json()["detail"] == "Action approval request not found."
        assert response.json()["error"]["code"] == "action_approval_not_found"
        assert "reauthentication" not in response.text
    rejection_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "approvals.action.approve",
                AuditLog.actor_user_id == seed_users["admin"].id,
                AuditLog.success.is_(False),
                AuditLog.metadata_json["reason"].as_string()
                == "action_approval_not_found",
            )
        ).all()
    )
    assert len(rejection_audits) == 2
    assert all(audit.resource_id is None for audit in rejection_audits)


def test_per_label_count_corruption_denies_and_audits_without_target_metadata(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    label, _feed, _run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    allowed = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(allowed):
        created = _post_approval(
            client,
            browser,
            key="approval-policy-audit-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert created.status_code == 201, created.text

    state = db_session.get(DataPolicyState, 1)
    approval = db_session.get(
        ActionApprovalRequest,
        uuid.UUID(created.json()["id"]),
    )
    assert state is not None and approval is not None
    state.coverage_version = 0
    db_session.add(state)
    db_session.commit()
    envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval.id,
    )
    assert envelope is not None
    merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval.id,
        sources=tuple(
            DataAccessSourceInput(
                source_type="unresolved",
                source_id=f"{approval.id}:count-corruption:{index}",
                source_version=f"test:count-corruption:{index}",
                handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
                captured_at=approval.created_at,
            )
            for index in range(2)
        ),
    )
    restricted_count = db_session.get(
        DataAccessEnvelopeLabel,
        (envelope.envelope_id, label.id),
    )
    quarantine_count = db_session.get(
        DataAccessEnvelopeLabel,
        (envelope.envelope_id, QUARANTINE_HANDLING_LABEL_ID),
    )
    assert restricted_count is not None and quarantine_count is not None
    restricted_count.source_count = 2
    quarantine_count.source_count = 1
    db_session.add_all([restricted_count, quarantine_count])
    db_session.commit()
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.coverage_version = 1
    db_session.add(state)
    db_session.commit()
    assert action_approval_data_policy_blocker_count(db_session) >= 1

    audit_access = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id, QUARANTINE_HANDLING_LABEL_ID},
        mode="audit",
    )
    enforced_access = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id, QUARANTINE_HANDLING_LABEL_ID},
    )
    with _override_data_access(enforced_access):
        hidden = client.get(
            f"/iam/action-approvals/{approval.id}",
            headers=browser,
        )
    assert hidden.status_code == 404
    with _override_data_access(audit_access):
        response = client.get(
            f"/iam/action-approvals/{approval.id}",
            headers=browser,
        )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(approval.id)

    evidence = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.resource_type == "action_approval",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert evidence is not None
    assert evidence.resource_id is None
    assert evidence.metadata_json["surface"] == "action_approval.detail"
    assert evidence.metadata_json["history_scope"] == "detail"
    assert evidence.metadata_json["affected_count"] == 1
    assert str(QUARANTINE_HANDLING_LABEL_ID) in evidence.data_access_label_ids
    assert not {
        "action_type",
        "target_id",
        "target_type",
        "target_snapshot",
    }.intersection(evidence.metadata_json)


def test_relabel_taints_approval_between_lifecycle_stages(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    first_label, feed, _run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    second_label = HandlingLabel(
        key=f"approval-relabel-{uuid.uuid4().hex[:12]}",
        name="Approval relabel target",
        color="#7C3AED",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
    )
    db_session.add(second_label)
    db_session.commit()
    browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    original_access = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={first_label.id},
    )
    with _override_data_access(original_access):
        created = _post_approval(
            client,
            browser,
            key="approval-policy-relabel-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert created.status_code == 201, created.text

    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=second_label.id,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()
    approval_id = uuid.UUID(created.json()["id"])
    envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval_id,
    )
    assert envelope is not None
    assert envelope.label_ids == {first_label.id, second_label.id}

    stale_grant = _context(
        db_session,
        principal_id=seed_users["admin"].id,
        allowed_label_ids={first_label.id},
    )
    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    with _override_data_access(stale_grant):
        denied = client.post(
            f"/iam/action-approvals/{approval_id}/decision",
            headers={
                **admin_browser,
                "Idempotency-Key": "approval-policy-relabel-denied",
            },
            json={
                "expected_revision": created.json()["revision"],
                "approve": True,
                "reason": "Independently reviewed target lineage.",
            },
        )
    assert denied.status_code == 404


def test_retention_pins_ai_lineage_until_approval_then_prunes_in_order(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    label, _feed, run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    system_run, system_receipt = _system_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    requester_access = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(requester_access):
        created = _post_approval(
            client,
            requester_browser,
            key="approval-policy-retention-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
        system_created = _post_approval(
            client,
            requester_browser,
            key="approval-policy-retention-system-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(system_receipt.id),
            target_revision=system_receipt.revision,
        )
    assert created.status_code == 201, created.text
    assert system_created.status_code == 201, system_created.text

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approver_access = _context(
        db_session,
        principal_id=seed_users["admin"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(approver_access):
        denied = client.post(
            f"/iam/action-approvals/{created.json()['id']}/decision",
            headers={
                **admin_browser,
                "Idempotency-Key": "approval-policy-retention-deny",
            },
            json={
                "expected_revision": created.json()["revision"],
                "approve": False,
                "reason": "Independent review rejected this target operation.",
            },
        )
        system_denied = client.post(
            f"/iam/action-approvals/{system_created.json()['id']}/decision",
            headers={
                **admin_browser,
                "Idempotency-Key": "approval-policy-retention-system-deny",
            },
            json={
                "expected_revision": system_created.json()["revision"],
                "approve": False,
                "reason": "Independent review rejected this system operation.",
            },
        )
    assert denied.status_code == 200, denied.text
    assert system_denied.status_code == 200, system_denied.text

    approval_id = uuid.UUID(created.json()["id"])
    system_approval_id = uuid.UUID(system_created.json()["id"])
    run_id = run.id
    receipt_id = receipt.id
    system_run_id = system_run.id
    system_receipt_id = system_receipt.id
    approval_envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval_id,
    )
    run_envelope = get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    )
    assert approval_envelope is not None and run_envelope is not None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=system_approval_id,
    ) is None
    parent_ids = set(
        db_session.scalars(
            select(DataAccessEnvelopeSource.id).where(
                DataAccessEnvelopeSource.envelope_id == run_envelope.envelope_id
            )
        ).all()
    )
    assert parent_ids
    assert set(
        db_session.scalars(
            select(DataAccessEnvelopeSource.source_parent_id).where(
                DataAccessEnvelopeSource.envelope_id
                == approval_envelope.envelope_id
            )
        ).all()
    ) == parent_ids

    now = datetime.now(timezone.utc)
    approval = db_session.get(ActionApprovalRequest, approval_id)
    system_approval = db_session.get(
        ActionApprovalRequest,
        system_approval_id,
    )
    assert approval is not None and system_approval is not None
    approval_old = now - timedelta(days=5)
    ai_old = now - timedelta(days=40)
    for retained_approval in (approval, system_approval):
        retained_approval.created_at = approval_old
        retained_approval.expires_at = approval_old + timedelta(hours=1)
        retained_approval.decided_at = approval_old + timedelta(minutes=30)
    for retained_run in (run, system_run):
        retained_run.status = "ready"
        retained_run.queued_at = ai_old
        retained_run.finished_at = ai_old + timedelta(minutes=1)
        retained_run.created_at = ai_old
        retained_run.updated_at = ai_old + timedelta(minutes=1)
    for retained_receipt in (receipt, system_receipt):
        retained_receipt.reconciliation_action = "acknowledged_may_have_sent"
        retained_receipt.reconciled_from_state = "ambiguous"
        retained_receipt.reconciled_from_io_outcome = "ambiguous"
        retained_receipt.reconciled_by_user_id_snapshot = seed_users["admin"].id
        retained_receipt.created_at = ai_old
        retained_receipt.settled_at = ai_old + timedelta(minutes=1)
        retained_receipt.reconciled_at = ai_old + timedelta(minutes=2)
        retained_receipt.updated_at = ai_old + timedelta(minutes=2)
        retained_receipt.revision += 1
    db_session.add_all(
        [
            approval,
            system_approval,
            run,
            system_run,
            receipt,
            system_receipt,
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.action_approval_retention_days",
        30,
    )
    monkeypatch.setattr(
        "app.services.history_maintenance.settings.ai_task_history_retention_days",
        30,
    )

    first = prune_application_history(db_session, now=now, batch_size=100)

    assert first.action_approval_requests_deleted == 0
    assert first.ai_task_runs_deleted == 0
    assert first.ai_provider_attempt_receipts_deleted == 0
    assert db_session.get(ActionApprovalRequest, approval_id) is not None
    assert db_session.get(ActionApprovalRequest, system_approval_id) is not None
    assert db_session.get(AITaskRun, run_id) is not None
    assert db_session.get(AITaskRun, system_run_id) is not None
    assert db_session.get(AIProviderAttemptReceipt, receipt_id) is not None
    assert db_session.get(AIProviderAttemptReceipt, system_receipt_id) is not None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval_id,
    ) is not None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    ) is not None

    monkeypatch.setattr(
        "app.services.history_maintenance.settings.action_approval_retention_days",
        1,
    )

    second = prune_application_history(db_session, now=now, batch_size=100)

    assert second.action_approval_requests_deleted == 2
    assert second.ai_task_runs_deleted == 2
    assert second.ai_provider_attempt_receipts_deleted == 2
    assert db_session.get(ActionApprovalRequest, approval_id) is None
    assert db_session.get(ActionApprovalRequest, system_approval_id) is None
    assert db_session.get(AITaskRun, run_id) is None
    assert db_session.get(AITaskRun, system_run_id) is None
    assert db_session.get(AIProviderAttemptReceipt, receipt_id) is None
    assert db_session.get(AIProviderAttemptReceipt, system_receipt_id) is None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
        resource_id=approval_id,
    ) is None
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        resource_id=run_id,
    ) is None


def test_execute_checks_approver_data_access_and_revision_before_side_effect(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch,
):
    _grant_requester(
        client,
        auth_headers["admin"],
        seed_users["viewer"].id,
    )
    label, _feed, _run, receipt = _restricted_receipt(
        db_session,
        actor_user_id=seed_users["admin"].id,
    )
    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    requester_access = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(requester_access):
        created = _post_approval(
            client,
            requester_browser,
            key="approval-policy-divergence-create",
            action_type="ai.provider_attempt.acknowledge_may_have_sent",
            target_id=str(receipt.id),
            target_revision=receipt.revision,
        )
    assert created.status_code == 201, created.text

    admin_browser = _browser_login(
        client,
        email="admin@example.com",
        password="AdminPass123!",
    )
    approver_access = _context(
        db_session,
        principal_id=seed_users["admin"].id,
        allowed_label_ids={label.id},
    )
    with _override_data_access(approver_access):
        approved = client.post(
            f"/iam/action-approvals/{created.json()['id']}/decision",
            headers={
                **admin_browser,
                "Idempotency-Key": "approval-policy-divergence-approve",
            },
            json={
                "expected_revision": created.json()["revision"],
                "approve": True,
                "reason": "Independently reviewed provider reconciliation.",
            },
        )
    assert approved.status_code == 200, approved.text

    denied_approver = _context(
        db_session,
        principal_id=seed_users["admin"].id,
        allowed_label_ids=set(),
    )
    with _override_data_access(denied_approver):
        decision_replay = client.post(
            f"/iam/action-approvals/{created.json()['id']}/decision",
            headers={
                **admin_browser,
                "Idempotency-Key": "approval-policy-divergence-approve",
            },
            json={
                "expected_revision": created.json()["revision"],
                "approve": True,
                "reason": "Independently reviewed provider reconciliation.",
            },
        )
    assert decision_replay.status_code == 404
    assert "target_snapshot" not in decision_replay.text

    requester_browser = _browser_login(
        client,
        email="viewer@example.com",
        password="ViewerPass123!",
    )
    original_fence = action_approval_service.fence_data_access_context
    monkeypatch.setattr(
        "app.services.action_approvals.data_access_context_for_authorization",
        lambda _db, _authorization: denied_approver,
    )
    with _override_data_access(requester_access):
        divergence = client.post(
            f"/iam/action-approvals/{created.json()['id']}/execute",
            headers={
                **requester_browser,
                "Idempotency-Key": "approval-policy-divergence-execute",
            },
            json={"expected_revision": approved.json()["revision"]},
        )
    assert divergence.status_code == 403, divergence.text
    db_session.refresh(receipt)
    assert receipt.reconciliation_action is None

    def revision_conflict(_db, _context):
        raise DataPolicyRevisionConflict(
            "Data policy changed before target execution.",
            current_revision=requester_access.policy_revision + 1,
        )

    monkeypatch.setattr(
        "app.services.action_approvals.fence_data_access_context",
        revision_conflict,
    )
    with _override_data_access(requester_access):
        raced = client.post(
            f"/iam/action-approvals/{created.json()['id']}/execute",
            headers={
                **requester_browser,
                "Idempotency-Key": "approval-policy-revision-race",
            },
            json={"expected_revision": approved.json()["revision"]},
        )
    assert raced.status_code == 409, raced.text
    db_session.refresh(receipt)
    assert receipt.reconciliation_action is None

    monkeypatch.setattr(
        action_approval_service,
        "data_access_context_for_authorization",
        lambda _db, _authorization: approver_access,
    )
    monkeypatch.setattr(
        action_approval_service,
        "fence_data_access_context",
        original_fence,
    )
    with _override_data_access(requester_access):
        executed = client.post(
            f"/iam/action-approvals/{created.json()['id']}/execute",
            headers={
                **requester_browser,
                "Idempotency-Key": "approval-policy-execute-replay",
            },
            json={"expected_revision": approved.json()["revision"]},
        )
    assert executed.status_code == 200, executed.text

    denied_requester = _context(
        db_session,
        principal_id=seed_users["viewer"].id,
        allowed_label_ids=set(),
    )
    with _override_data_access(denied_requester):
        execute_replay = client.post(
            f"/iam/action-approvals/{created.json()['id']}/execute",
            headers={
                **requester_browser,
                "Idempotency-Key": "approval-policy-execute-replay",
            },
            json={"expected_revision": approved.json()["revision"]},
        )
    assert execute_replay.status_code == 404
    assert "target_snapshot" not in execute_replay.text
