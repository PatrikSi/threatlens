from __future__ import annotations

from sqlalchemy import func, select

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.audit_log import AuditLog
from app.models.data_policy import HandlingLabel, UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed


def _browser_login(client) -> dict[str, str]:
    client.cookies.clear()
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert response.status_code == 200, response.text
    csrf_token = response.json().get("csrf_token")
    assert csrf_token
    return {"X-CSRF-Token": csrf_token}


def _feed(name: str, url: str) -> Feed:
    feed = Feed(name=name)
    feed.url = url
    return feed


def test_data_policy_read_permissions_and_idempotent_audited_mutations(
    client,
    auth_headers,
    seed_users,
    db_session,
):
    forbidden = client.get(
        "/iam/data-policies",
        headers=auth_headers["analyst"],
    )
    assert forbidden.status_code == 403

    overview_response = client.get(
        "/iam/data-policies",
        headers=auth_headers["admin"],
    )
    assert overview_response.status_code == 200, overview_response.text
    overview = overview_response.json()
    assert overview["state"]["mode"] == "disabled"
    assert overview["preflight"]["ready_for_enforcement"] is False

    token_attempt = client.post(
        "/iam/data-policies/labels",
        headers={
            **auth_headers["admin"],
            "Idempotency-Key": "data-policy-token-create-1",
        },
        json={
            "expected_policy_revision": overview["state"]["revision"],
            "key": "token-label",
            "name": "Token label",
        },
    )
    assert token_attempt.status_code == 403, token_attempt.text
    assert token_attempt.json()["error"]["code"] == "browser_session_required"

    browser_headers = _browser_login(client)
    create_payload = {
        "expected_policy_revision": overview["state"]["revision"],
        "key": "restricted-operations",
        "name": "Restricted operations",
        "description": "Operationally restricted source material.",
        "role_ids": [],
    }
    created = client.post(
        "/iam/data-policies/labels",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-browser-create-1",
        },
        json=create_payload,
    )
    assert created.status_code == 201, created.text
    assert created.headers["X-ThreatLens-Mutation-Changed"] == "true"
    body = created.json()
    assert body["label"]["key"] == "restricted-operations"
    assert body["changed"] is True
    assert len(body["label"]["role_ids"]) == 1

    explicit_null = client.patch(
        f"/iam/data-policies/labels/{body['label']['id']}",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-null-update-1",
        },
        json={"expected_revision": body["label"]["revision"], "name": None},
    )
    assert explicit_null.status_code == 422, explicit_null.text

    whitespace_reason = client.put(
        "/iam/data-policies/mode",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-empty-reason-1",
        },
        json={
            "expected_revision": body["policy_revision"],
            "mode": "disabled",
            "reason": "   ",
        },
    )
    assert whitespace_reason.status_code == 422, whitespace_reason.text

    replay = client.post(
        "/iam/data-policies/labels",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-browser-create-1",
        },
        json=create_payload,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == body
    assert replay.headers["X-ThreatLens-Mutation-Changed"] == "false"

    conflicting_reuse = client.post(
        "/iam/data-policies/labels",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-browser-create-1",
        },
        json={**create_payload, "name": "Different request"},
    )
    assert conflicting_reuse.status_code == 409, conflicting_reuse.text
    assert (
        conflicting_reuse.json()["error"]["code"] == "governance_idempotency_conflict"
    )

    grants = client.put(
        f"/iam/data-policies/labels/{body['label']['id']}/role-grants",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-role-grants-1",
        },
        json={
            "expected_revision": body["label"]["revision"],
            "role_ids": [
                str(SYSTEM_ROLE_IDS["admin"]),
                str(SYSTEM_ROLE_IDS["analyst"]),
            ],
        },
    )
    assert grants.status_code == 200, grants.text
    grants_body = grants.json()
    assert grants_body["policy_revision"] == body["policy_revision"] + 1

    replay_after_change = client.post(
        "/iam/data-policies/labels",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-browser-create-1",
        },
        json=create_payload,
    )
    assert replay_after_change.status_code == 201, replay_after_change.text
    assert replay_after_change.json() == body
    assert (
        int(replay_after_change.headers["X-Current-Revision"])
        == grants_body["policy_revision"]
    )

    feed = _feed("Policy assignment", "https://example.com/policy-assignment.xml")
    db_session.add(feed)
    db_session.flush()
    assigned = client.put(
        f"/iam/data-policies/feeds/{feed.id}",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-feed-assignment-1",
        },
        json={
            "expected_policy_revision": grants_body["policy_revision"],
            "handling_label_id": body["label"]["id"],
        },
    )
    assert assigned.status_code == 200, assigned.text
    assigned_body = assigned.json()
    assert assigned_body["previous_handling_label_id"] == str(
        UNRESTRICTED_HANDLING_LABEL_ID
    )
    assert assigned_body["handling_label_id"] == body["label"]["id"]

    archive_blocked = client.put(
        f"/iam/data-policies/labels/{body['label']['id']}/status",
        headers={
            **browser_headers,
            "Idempotency-Key": "data-policy-archive-assigned-1",
        },
        json={
            "expected_revision": grants_body["label"]["revision"],
            "active": False,
        },
    )
    assert archive_blocked.status_code == 409, archive_blocked.text
    assert archive_blocked.json()["error"]["code"] == "data_policy_conflict"

    assert (
        db_session.scalar(
            select(func.count(HandlingLabel.id)).where(
                HandlingLabel.key == "restricted-operations"
            )
        )
        == 1
    )

    audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action.in_(
                [
                    "data_policy.label.create",
                    "data_policy.label.role_grants.replace",
                    "data_policy.feed.assign",
                    "data_policy.label.archive",
                ]
            )
        )
    ).all()
    assert any(audit.success for audit in audits)
    assert any(not audit.success for audit in audits)
    assert all("description" not in (audit.metadata_json or {}) for audit in audits)
    grant_audit = next(
        audit
        for audit in audits
        if audit.action == "data_policy.label.role_grants.replace" and audit.success
    )
    assert grant_audit.metadata_json["added_role_ids"] == [
        str(SYSTEM_ROLE_IDS["analyst"])
    ]
    assert grant_audit.metadata_json["removed_role_ids"] == []
    feed_audit = next(
        audit
        for audit in audits
        if audit.action == "data_policy.feed.assign" and audit.success
    )
    assert feed_audit.metadata_json["previous_handling_label_id"] == str(
        UNRESTRICTED_HANDLING_LABEL_ID
    )


def test_data_policy_mutations_publish_browser_only_openapi_contract(client):
    schema = client.get("/openapi.json").json()
    create_operation = schema["paths"]["/v1/iam/data-policies/labels"]["post"]
    assign_operation = schema["paths"]["/v1/iam/data-policies/feeds/{feed_id}"]["put"]

    for operation in (create_operation, assign_operation):
        assert operation["security"] == [{"SessionCookieAuth": []}]
        assert "x-threatlens-required-token-scopes" not in operation
        assert "409" in operation["responses"]
        assert "503" in operation["responses"]
