from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.audit_log import AuditLog
from app.models.data_policy import DataPolicyRoleGrant, DataPolicyState, HandlingLabel
from app.models.feed import Feed
from app.models.iam import (
    IAMPolicyState,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
from app.services import data_access_policy
from app.services.audit import record_audit


def _restricted_audit_fixture(db_session, seed_users, *, mode: str):
    audit_reader = IAMRole(
        key=f"audit-reader-{uuid.uuid4().hex[:12]}",
        name="Audit reader without restricted labels",
        description="Test-only audit reader role.",
        is_system=False,
        created_by_user_id=seed_users["admin"].id,
    )
    db_session.add(audit_reader)
    db_session.flush()
    db_session.add_all(
        [
            IAMRolePermission(role_id=audit_reader.id, permission="read:audit"),
            IAMUserRoleAssignment(
                user_id=seed_users["analyst"].id,
                role_id=audit_reader.id,
                source="local",
                source_key="",
            ),
        ]
    )
    iam_state = db_session.get(IAMPolicyState, 1)
    assert iam_state is not None
    iam_state.revision += 1

    restricted = HandlingLabel(
        key=f"audit-restricted-{uuid.uuid4().hex[:12]}",
        name="Restricted audit history",
        description="Restricted audit test label.",
        color="#991B1B",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
        updated_by_user_id=seed_users["admin"].id,
    )
    db_session.add(restricted)
    db_session.flush()
    db_session.add(
        DataPolicyRoleGrant(
            label_id=restricted.id,
            role_id=SYSTEM_ROLE_IDS["admin"],
            granted_by_user_id=seed_users["admin"].id,
        )
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = mode
    state.coverage_version = 1
    state.revision += 1
    state.enforced_at = datetime.now(timezone.utc) if mode == "enforced" else None
    state.enforced_by_user_id = seed_users["admin"].id if mode == "enforced" else None
    state.updated_by_user_id = seed_users["admin"].id

    feed = Feed(
        name="Restricted audit source",
        url=f"https://restricted.example/{uuid.uuid4()}.xml",
        handling_label_id=restricted.id,
    )
    db_session.add(feed)
    db_session.flush()
    audit = record_audit(
        db_session,
        actor_user_id=seed_users["admin"].id,
        request_id="restricted-audit-request",
        action="test.restricted.resource",
        resource_type="feed",
        resource_id=str(feed.id),
        resource_label_snapshot="Restricted audit source",
        metadata={
            "title": "Restricted source title",
            "url": "https://restricted.example/private",
        },
    )
    db_session.commit()
    assert audit.data_access_governed is True
    assert audit.data_access_label_ids == [str(restricted.id)]

    db_session.delete(feed)
    db_session.commit()
    return restricted, audit


def test_audit_history_redacts_durable_restricted_snapshots_after_source_deletion(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    restricted, audit = _restricted_audit_fixture(
        db_session,
        seed_users,
        mode="enforced",
    )
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )

    response = client.get(
        "/audit-logs?action=test.restricted.resource",
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    projected = payload["logs"][0]
    assert projected["id"] == str(audit.id)
    assert projected["resource_type"] == "feed"
    assert projected["resource_id"] is None
    assert projected["resource_label_snapshot"] is None
    assert projected["request_id"] is None
    assert projected["data_access_redacted"] is True
    assert projected["metadata_json"] == {
        "data_access_redacted": True,
        "reason": "handling_label_access_required",
    }
    assert "Restricted source title" not in response.text
    assert "restricted.example" not in response.text

    exported = client.get(
        "/audit-logs/export?action=test.restricted.resource&resource_id="
        f"{audit.resource_id}",
        headers=auth_headers["analyst"],
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["logs"][0]["data_access_redacted"] is True
    assert "restricted.example" not in exported.text

    db_session.expire_all()
    decision = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "data_policy.access.not_served",
            AuditLog.resource_type == "audit_log",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert decision is not None
    assert decision.metadata_json["surface"] in {"audit.list", "audit.export"}
    assert decision.metadata_json["affected_count"] == 1
    assert decision.data_access_governed is True
    assert decision.data_access_label_ids == [str(restricted.id)]

    export_audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "audit.export")
        .order_by(AuditLog.created_at.desc())
    )
    assert export_audit is not None
    assert export_audit.metadata_json["filters"]["resource_id_supplied"] is True
    assert "resource_id" not in export_audit.metadata_json["filters"]

    admin = client.get(
        "/audit-logs?action=test.restricted.resource",
        headers=auth_headers["admin"],
    )
    assert admin.status_code == 200, admin.text
    assert admin.json()["logs"][0]["resource_id"] == audit.resource_id
    assert admin.json()["logs"][0]["resource_label_snapshot"] == (
        "Restricted audit source"
    )
    assert admin.json()["logs"][0]["metadata_json"]["title"] == (
        "Restricted source title"
    )
    assert admin.json()["logs"][0]["data_access_redacted"] is False


def test_audit_mode_serves_restricted_history_and_records_would_deny(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    restricted, audit = _restricted_audit_fixture(
        db_session,
        seed_users,
        mode="audit",
    )
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )

    response = client.get(
        "/audit-logs?action=test.restricted.resource",
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    projected = response.json()["logs"][0]
    assert projected["resource_id"] == audit.resource_id
    assert projected["metadata_json"]["title"] == "Restricted source title"
    assert projected["data_access_redacted"] is False

    db_session.expire_all()
    decision = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.resource_type == "audit_log",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert decision is not None
    assert decision.metadata_json["surface"] == "audit.list"
    assert decision.metadata_json["request_served"] is True
    assert decision.data_access_label_ids == [str(restricted.id)]
