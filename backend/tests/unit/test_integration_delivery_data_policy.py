from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.audit_log import AuditLog
from app.models.data_policy import DataPolicyRoleGrant, DataPolicyState, HandlingLabel
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.services import data_access_policy
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DataAccessSourceInput,
    merge_data_access_envelope_sources,
)
from app.services.integration_delivery_data_policy import (
    IntegrationDeliveryDataPolicyDenied,
    enforce_integration_delivery_data_policy,
    record_integration_delivery_policy_audit,
)


def test_system_delivery_audit_mode_allows_and_deduplicates_would_deny(
    db_session,
    seed_users,
    monkeypatch,
):
    instance, delivery = _restricted_system_delivery(
        db_session,
        seed_users=seed_users,
        mode="audit",
        monkeypatch=monkeypatch,
    )

    first = enforce_integration_delivery_data_policy(
        db_session,
        instance=instance,
        delivery=delivery,
        surface="smtp.external_io",
    )
    second = enforce_integration_delivery_data_policy(
        db_session,
        instance=instance,
        delivery=delivery,
        surface="smtp.external_io",
    )

    assert first.allowed is True
    assert first.would_deny is True
    assert second.would_deny is True
    logs = _policy_logs(db_session, delivery_id=delivery.id)
    assert len(logs) == 1
    assert logs[0].action == "data_policy.egress.would_deny"
    assert logs[0].actor_principal_type == "integration_instance"
    assert logs[0].actor_principal_id == instance.id
    assert logs[0].metadata_json["surface"] == "smtp.external_io"


def test_system_delivery_enforcement_denial_can_be_restored_after_rollback(
    db_session,
    seed_users,
    monkeypatch,
):
    instance, delivery = _restricted_system_delivery(
        db_session,
        seed_users=seed_users,
        mode="enforced",
        monkeypatch=monkeypatch,
    )

    with pytest.raises(IntegrationDeliveryDataPolicyDenied) as captured:
        enforce_integration_delivery_data_policy(
            db_session,
            instance=instance,
            delivery=delivery,
            surface="smtp.external_io",
        )

    assert len(_policy_logs(db_session, delivery_id=delivery.id)) == 1
    audit = captured.value.audit
    assert audit is not None
    db_session.rollback()
    assert _policy_logs(db_session, delivery_id=delivery.id) == []

    record_integration_delivery_policy_audit(db_session, audit=audit)
    record_integration_delivery_policy_audit(db_session, audit=audit)
    db_session.commit()

    logs = _policy_logs(db_session, delivery_id=delivery.id)
    assert len(logs) == 1
    assert logs[0].action == "data_policy.egress.denied"
    assert logs[0].success is False
    assert logs[0].metadata_json["request_served"] is False


def _restricted_system_delivery(
    db_session,
    *,
    seed_users,
    mode: str,
    monkeypatch,
) -> tuple[IntegrationInstance, IntegrationDelivery]:
    label = HandlingLabel(
        key=f"delivery-{uuid.uuid4().hex[:12]}",
        name="Restricted delivery",
        description="Outbound delivery policy test label.",
        color="#B91C1C",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
        updated_by_user_id=seed_users["admin"].id,
    )
    instance = IntegrationInstance(
        name="System policy delivery",
        integration_type="smtp",
        direction="outbound",
        enabled=True,
    )
    db_session.add_all([label, instance])
    db_session.flush()
    db_session.add(
        DataPolicyRoleGrant(
            label_id=label.id,
            role_id=SYSTEM_ROLE_IDS["admin"],
            granted_by_user_id=seed_users["admin"].id,
        )
    )
    delivery = IntegrationDelivery(
        integration_id=instance.id,
        connector_type="smtp",
        event_type="daily_digest",
        delivery_kind="live",
        state="sending",
        idempotency_key=f"policy-delivery:{uuid.uuid4()}",
        attempt_count=1,
    )
    db_session.add(delivery)
    db_session.flush()

    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = mode
    state.coverage_version = 1
    state.revision += 1
    state.enforced_at = datetime.now(timezone.utc) if mode == "enforced" else None
    state.enforced_by_user_id = seed_users["admin"].id if mode == "enforced" else None
    state.updated_by_user_id = seed_users["admin"].id
    db_session.flush()
    merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=delivery.id,
        sources=(
            DataAccessSourceInput(
                source_type="test_fixture",
                source_id=str(uuid.uuid4()),
                source_version="1",
                handling_label_id=label.id,
                captured_policy_revision=state.revision,
            ),
        ),
    )
    db_session.commit()
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )
    return instance, delivery


def _policy_logs(db_session, *, delivery_id: uuid.UUID) -> list[AuditLog]:
    count = db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.resource_type == DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            AuditLog.resource_id == str(delivery_id),
            AuditLog.action.in_(
                {
                    "data_policy.egress.denied",
                    "data_policy.egress.would_deny",
                }
            ),
        )
    )
    if not count:
        return []
    return list(
        db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
                AuditLog.resource_id == str(delivery_id),
                AuditLog.action.in_(
                    {
                        "data_policy.egress.denied",
                        "data_policy.egress.would_deny",
                    }
                ),
            )
            .order_by(AuditLog.created_at.asc())
        ).all()
    )
