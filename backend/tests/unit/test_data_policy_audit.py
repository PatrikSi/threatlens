from __future__ import annotations

import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.services.data_access_policy import DataAccessContext
from app.services.data_policy_audit import record_data_policy_decision


def _context(
    mode: str,
    *,
    principal_type: str = "user",
    principal_id: uuid.UUID | None = None,
) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,  # type: ignore[arg-type]
        policy_revision=12,
        coverage_version=1,
        principal_type=principal_type,
        principal_id=principal_id or uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )


def test_policy_audit_records_diagnostics_without_accepting_resource_payloads(
    db_session,
    seed_users,
):
    context = _context("audit", principal_id=seed_users["admin"].id)
    envelope_id = uuid.uuid4()

    recorded = record_data_policy_decision(
        db_session,
        context=context,
        decision="would_deny",
        resource_type="report",
        resource_id=uuid.uuid4(),
        surface="reports.download",
        handling_label_ids=[QUARANTINE_HANDLING_LABEL_ID],
        envelope_id=envelope_id,
        affected_count=1,
    )
    db_session.flush()

    stored = db_session.get(AuditLog, recorded.id)
    assert stored is not None
    assert stored.actor_user_id == context.principal_id
    assert stored.actor_principal_type == "user"
    assert stored.action == "data_policy.access.would_deny"
    assert stored.success is True
    assert stored.metadata_json == {
        "decision": "would_deny",
        "surface": "reports.download",
        "data_policy_mode": "audit",
        "data_policy_revision": 12,
        "data_policy_coverage_version": 1,
        "request_served": True,
        "handling_label_count": 1,
        "handling_label_ids": [str(QUARANTINE_HANDLING_LABEL_ID)],
        "envelope_id": str(envelope_id),
        "affected_count": 1,
    }
    assert set(stored.metadata_json).isdisjoint(
        {"payload", "title", "url", "prompt", "body", "content"}
    )


def test_policy_audit_preserves_service_account_identity_without_user_fk(
    db_session,
):
    context = _context("enforced", principal_type="service_account")

    recorded = record_data_policy_decision(
        db_session,
        context=context,
        decision="egress_denied",
        resource_type="integration_delivery",
        resource_id=uuid.uuid4(),
        surface="integrations.smtp.send",
    )
    db_session.flush()

    assert recorded.actor_user_id is None
    assert recorded.actor_principal_type == "service_account"
    assert recorded.actor_principal_id == context.principal_id
    assert recorded.success is False


def test_policy_audit_records_active_mode_egress_that_was_not_served(db_session):
    context = _context("audit", principal_type="ai_worker")

    recorded = record_data_policy_decision(
        db_session,
        context=context,
        decision="egress_not_served",
        resource_type="report",
        resource_id=uuid.uuid4(),
        surface="ai_provider.external_io",
    )
    db_session.flush()

    assert recorded.action == "data_policy.egress.not_served"
    assert recorded.success is False
    assert recorded.metadata_json["request_served"] is False


@pytest.mark.parametrize("reserved_key", ["request_served", "handling_label_ids"])
def test_policy_audit_rejects_reserved_extra_metadata_even_when_key_would_be_omitted(
    db_session,
    reserved_key,
):
    with pytest.raises(ValueError, match="reserved keys"):
        record_data_policy_decision(
            db_session,
            context=_context("audit"),
            decision="egress_would_deny",
            resource_type="report",
            surface="ai_provider.external_io",
            request_served_known=False,
            metadata_extra={reserved_key: "caller-controlled"},
        )


@pytest.mark.parametrize(
    ("mode", "decision"),
    [
        ("disabled", "not_served"),
        ("audit", "not_served"),
        ("disabled", "would_deny"),
        ("enforced", "would_deny"),
        ("disabled", "egress_not_served"),
    ],
)
def test_policy_audit_rejects_decisions_in_the_wrong_mode(
    db_session,
    mode,
    decision,
):
    with pytest.raises(ValueError):
        record_data_policy_decision(
            db_session,
            context=_context(mode),
            decision=decision,
            resource_type="item",
            surface="items.detail",
        )
