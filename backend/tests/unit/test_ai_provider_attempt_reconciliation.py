from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.services.action_registry import (
    ACTION_DEFINITIONS,
    RegisteredActionTargetConflict,
    RegisteredActionTargetNotFound,
    execute_registered_action,
    get_registered_action,
    inspect_registered_action_target,
)
from app.services.ai_provider_attempts import (
    AIProviderAttemptReconciliationError,
    AIProviderAttemptStateError,
    lock_ai_provider_attempt_for_io,
    reconcile_ai_provider_attempt,
    reserve_ai_provider_attempt,
    settle_ai_provider_attempt,
    void_ai_provider_attempt_reservation,
)


_CONFIRM_ACTION = "ai.provider_attempt.confirm_not_sent"
_ACKNOWLEDGE_ACTION = "ai.provider_attempt.acknowledge_may_have_sent"


def _receipt(
    *,
    attempt_number: int = 1,
    max_attempts: int = 3,
    state: str = "reserved",
    io_outcome: str = "reserved",
    retryable: bool | None = None,
    settled_at: datetime | None = None,
) -> AIProviderAttemptReceipt:
    return AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(),
        attempt_number=attempt_number,
        request_fingerprint="a" * 64,
        task_run_id_snapshot=uuid.uuid4(),
        feature_type="item_enrichment",
        resource_type="item",
        resource_id=uuid.uuid4(),
        max_attempts=max_attempts,
        requested_max_tokens=1_024,
        iam_revision=2,
        data_policy_revision=3,
        data_policy_mode="enforced",
        state=state,
        io_outcome=io_outcome,
        retryable=retryable,
        next_max_tokens=None,
        settled_at=settled_at,
    )


def test_provider_receipt_settlement_advances_revision(db_session):
    receipt = _receipt()
    db_session.add(receipt)
    db_session.flush()

    with pytest.raises(AIProviderAttemptStateError) as invalid_generation:
        settle_ai_provider_attempt(
            db_session,
            receipt_id=receipt.id,
            request_fingerprint=receipt.request_fingerprint,
            state="succeeded",
            io_outcome="response_received",
            retryable=False,
            reservation_generation=0,
        )
    assert invalid_generation.value.category == "stale_generation"

    settled = settle_ai_provider_attempt(
        db_session,
        receipt_id=receipt.id,
        request_fingerprint=receipt.request_fingerprint,
        state="succeeded",
        io_outcome="response_received",
        retryable=False,
        reservation_generation=receipt.reservation_generation,
    )

    assert settled.revision == 2
    assert settled.state == "succeeded"


def test_pre_io_void_reopens_same_attempt_with_fresh_policy_snapshot(db_session):
    task_run = AITaskRun(
        task_type="connection_test",
        trigger_source="manual",
        status="running",
        metadata_json={},
    )
    db_session.add(task_run)
    db_session.flush()
    fingerprint = "b" * 64
    initial = reserve_ai_provider_attempt(
        db_session,
        task_run_id=task_run.id,
        feature_type="connection_test",
        item_id=None,
        daily_brief_id=None,
        report_id=None,
        operation_scope="connection_test",
        attempt_number=1,
        max_attempts=3,
        requested_max_tokens=512,
        request_fingerprint=fingerprint,
        iam_revision=2,
        data_policy_revision=3,
        data_policy_mode="audit",
    )
    assert initial.attempt_number == 1
    assert initial.reservation_generation == 1

    with pytest.raises(AIProviderAttemptStateError) as unresolved:
        reserve_ai_provider_attempt(
            db_session,
            task_run_id=task_run.id,
            feature_type="connection_test",
            item_id=None,
            daily_brief_id=None,
            report_id=None,
            operation_scope="connection_test",
            attempt_number=1,
            max_attempts=3,
            requested_max_tokens=512,
            request_fingerprint=fingerprint,
            iam_revision=2,
            data_policy_revision=3,
            data_policy_mode="audit",
        )
    assert unresolved.value.category == "reconciliation_required"
    assert unresolved.value.requires_reconciliation is True

    with pytest.raises(AIProviderAttemptStateError) as invalid_generation:
        void_ai_provider_attempt_reservation(
            db_session,
            receipt_id=initial.receipt_id,
            request_fingerprint=fingerprint,
            reservation_generation=0,
        )
    assert invalid_generation.value.category == "stale_generation"

    voided = void_ai_provider_attempt_reservation(
        db_session,
        receipt_id=initial.receipt_id,
        request_fingerprint=fingerprint,
        reservation_generation=initial.reservation_generation,
    )
    first_failure_at = voided.last_pre_io_failure_at
    assert voided.state == "voided"
    assert voided.io_outcome == "not_sent"
    assert voided.retryable is True
    assert voided.next_max_tokens is None
    assert voided.settled_at == first_failure_at
    assert voided.pre_io_failure_count == 1
    assert voided.reservation_generation == 1
    assert voided.revision == 2

    reopened = reserve_ai_provider_attempt(
        db_session,
        task_run_id=task_run.id,
        feature_type="connection_test",
        item_id=None,
        daily_brief_id=None,
        report_id=None,
        operation_scope="connection_test",
        attempt_number=1,
        max_attempts=9,
        requested_max_tokens=512,
        request_fingerprint=fingerprint,
        iam_revision=4,
        data_policy_revision=5,
        data_policy_mode="enforced",
    )

    assert reopened.receipt_id == initial.receipt_id
    assert reopened.attempt_number == 1
    assert reopened.max_attempts == 3
    assert reopened.reservation_generation == 2
    assert reopened.resumed_safe_failure is False
    receipt = db_session.get(AIProviderAttemptReceipt, initial.receipt_id)
    assert receipt is not None
    assert receipt.state == "reserved"
    assert receipt.io_outcome == "reserved"
    assert receipt.retryable is None
    assert receipt.settled_at is None
    assert receipt.pre_io_failure_count == 1
    assert receipt.last_pre_io_failure_at == first_failure_at
    assert receipt.iam_revision == 4
    assert receipt.data_policy_revision == 5
    assert receipt.data_policy_mode == "enforced"
    assert receipt.revision == 3

    with pytest.raises(AIProviderAttemptStateError):
        lock_ai_provider_attempt_for_io(
            db_session,
            reservation=initial,
            task_run_id=task_run.id,
            feature_type="connection_test",
            item_id=None,
            daily_brief_id=None,
            report_id=None,
            request_fingerprint=fingerprint,
        )
    assert (
        lock_ai_provider_attempt_for_io(
            db_session,
            reservation=reopened,
            task_run_id=task_run.id,
            feature_type="connection_test",
            item_id=None,
            daily_brief_id=None,
            report_id=None,
            request_fingerprint=fingerprint,
        ).id
        == receipt.id
    )

    with pytest.raises(AIProviderAttemptStateError) as stale:
        void_ai_provider_attempt_reservation(
            db_session,
            receipt_id=initial.receipt_id,
            request_fingerprint=fingerprint,
            reservation_generation=1,
        )
    assert stale.value.category == "stale_generation"
    assert receipt.state == "reserved"


def test_provider_attempt_state_errors_distinguish_reconciliation_and_terminal_states(
    db_session,
):
    receipts = {
        "ambiguous": _receipt(
            state="ambiguous",
            io_outcome="ambiguous",
            retryable=False,
            settled_at=datetime.now(timezone.utc),
        ),
        "succeeded": _receipt(
            state="succeeded",
            io_outcome="response_received",
            retryable=False,
            settled_at=datetime.now(timezone.utc),
        ),
        "failed": _receipt(
            state="failed",
            io_outcome="not_sent",
            retryable=False,
            settled_at=datetime.now(timezone.utc),
        ),
    }
    db_session.add_all(receipts.values())
    db_session.flush()

    for state, receipt in receipts.items():
        with pytest.raises(AIProviderAttemptStateError) as captured:
            settle_ai_provider_attempt(
                db_session,
                receipt_id=receipt.id,
                request_fingerprint=receipt.request_fingerprint,
                state="succeeded",
                io_outcome="response_received",
                retryable=False,
                reservation_generation=receipt.reservation_generation,
            )
        if state == "ambiguous":
            assert captured.value.category == "reconciliation_required"
            assert captured.value.requires_reconciliation is True
        else:
            assert captured.value.category in {
                "already_succeeded",
                "terminal_failed",
            }
            assert captured.value.requires_reconciliation is False


def test_confirm_not_sent_preserves_unsafe_evidence_and_allows_safe_retry(
    db_session,
):
    actor_id = uuid.uuid4()
    receipt = _receipt()
    db_session.add(receipt)
    db_session.flush()

    reconciled = reconcile_ai_provider_attempt(
        db_session,
        receipt_id=receipt.id,
        expected_revision=1,
        action="confirmed_not_sent",
        actor_user_id=actor_id,
    )

    assert reconciled.revision == 2
    assert reconciled.reconciliation_action == "confirmed_not_sent"
    assert reconciled.reconciled_from_state == "reserved"
    assert reconciled.reconciled_from_io_outcome == "reserved"
    assert reconciled.reconciled_by_user_id_snapshot == actor_id
    assert reconciled.reconciled_at is not None
    assert reconciled.state == "failed"
    assert reconciled.io_outcome == "not_sent"
    assert reconciled.retryable is True
    assert reconciled.next_max_tokens == 1_024
    assert reconciled.settled_at == reconciled.reconciled_at


def test_confirm_not_sent_is_truthfully_terminal_when_budget_is_exhausted(
    db_session,
):
    receipt = _receipt(attempt_number=1, max_attempts=1)
    db_session.add(receipt)
    db_session.flush()

    reconciled = reconcile_ai_provider_attempt(
        db_session,
        receipt_id=receipt.id,
        expected_revision=1,
        action="confirmed_not_sent",
        actor_user_id=uuid.uuid4(),
    )

    assert reconciled.state == "failed"
    assert reconciled.io_outcome == "not_sent"
    assert reconciled.retryable is False
    assert reconciled.next_max_tokens is None


def test_acknowledge_may_have_sent_keeps_outcome_ambiguous(db_session):
    original_settled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    receipt = _receipt(
        state="ambiguous",
        io_outcome="ambiguous",
        retryable=False,
        settled_at=original_settled_at,
    )
    db_session.add(receipt)
    db_session.flush()

    reconciled = reconcile_ai_provider_attempt(
        db_session,
        receipt_id=receipt.id,
        expected_revision=1,
        action="acknowledged_may_have_sent",
        actor_user_id=uuid.uuid4(),
    )

    assert reconciled.revision == 2
    assert reconciled.reconciliation_action == "acknowledged_may_have_sent"
    assert reconciled.reconciled_from_state == "ambiguous"
    assert reconciled.reconciled_from_io_outcome == "ambiguous"
    assert reconciled.state == "ambiguous"
    assert reconciled.io_outcome == "ambiguous"
    assert reconciled.retryable is False
    assert reconciled.next_max_tokens is None
    assert reconciled.settled_at == original_settled_at


def test_reconciliation_rejects_missing_stale_resolved_and_safe_receipts(db_session):
    missing_id = uuid.uuid4()
    with pytest.raises(AIProviderAttemptReconciliationError, match="not found"):
        reconcile_ai_provider_attempt(
            db_session,
            receipt_id=missing_id,
            expected_revision=1,
            action="confirmed_not_sent",
            actor_user_id=uuid.uuid4(),
        )

    unsafe = _receipt()
    safe = _receipt(
        state="succeeded",
        io_outcome="response_received",
        retryable=False,
        settled_at=datetime.now(timezone.utc),
    )
    db_session.add_all([unsafe, safe])
    db_session.flush()

    with pytest.raises(AIProviderAttemptReconciliationError, match="changed"):
        reconcile_ai_provider_attempt(
            db_session,
            receipt_id=unsafe.id,
            expected_revision=0,
            action="confirmed_not_sent",
            actor_user_id=uuid.uuid4(),
        )
    with pytest.raises(AIProviderAttemptReconciliationError, match="changed"):
        reconcile_ai_provider_attempt(
            db_session,
            receipt_id=unsafe.id,
            expected_revision=2,
            action="confirmed_not_sent",
            actor_user_id=uuid.uuid4(),
        )
    with pytest.raises(AIProviderAttemptReconciliationError, match="Only an unresolved"):
        reconcile_ai_provider_attempt(
            db_session,
            receipt_id=safe.id,
            expected_revision=1,
            action="confirmed_not_sent",
            actor_user_id=uuid.uuid4(),
        )

    resolved = reconcile_ai_provider_attempt(
        db_session,
        receipt_id=unsafe.id,
        expected_revision=1,
        action="acknowledged_may_have_sent",
        actor_user_id=uuid.uuid4(),
    )
    with pytest.raises(AIProviderAttemptReconciliationError, match="Only an unresolved"):
        reconcile_ai_provider_attempt(
            db_session,
            receipt_id=resolved.id,
            expected_revision=resolved.revision,
            action="acknowledged_may_have_sent",
            actor_user_id=uuid.uuid4(),
        )


def test_reconciliation_actions_have_fixed_critical_registry_contracts():
    definitions = {definition.key: definition for definition in ACTION_DEFINITIONS}
    assert set(definitions) == {
        _ACKNOWLEDGE_ACTION,
        _CONFIRM_ACTION,
        "iam.role.delete",
        "service_account.disable",
    }
    for action_key in (_CONFIRM_ACTION, _ACKNOWLEDGE_ACTION):
        definition = definitions[action_key]
        assert definition.version == 1
        assert definition.target_type == "ai_provider_attempt_receipt"
        assert definition.audit_action == action_key
        assert definition.requester_permission == "read:ai"
        assert definition.approver_permission == "write:ai"
        assert definition.risk == "critical"
        assert definition.payload_fields == ()


@pytest.mark.parametrize(
    ("action_key", "expected_action", "expected_state", "expected_outcome"),
    [
        (
            _CONFIRM_ACTION,
            "confirmed_not_sent",
            "failed",
            "not_sent",
        ),
        (
            _ACKNOWLEDGE_ACTION,
            "acknowledged_may_have_sent",
            "ambiguous",
            "ambiguous",
        ),
    ],
)
def test_registry_reconciliation_snapshots_evidence_and_executes_fixed_action(
    db_session,
    action_key,
    expected_action,
    expected_state,
    expected_outcome,
):
    receipt = _receipt()
    db_session.add(receipt)
    db_session.flush()
    definition = get_registered_action(action_key)

    target = inspect_registered_action_target(
        db_session,
        definition=definition,
        target_id=str(receipt.id),
        target_revision=1,
    )

    assert target.snapshot["request_fingerprint"] == receipt.request_fingerprint
    assert target.snapshot["operation_id"] == str(receipt.operation_id)
    assert target.snapshot["state"] == "reserved"
    assert target.snapshot["io_outcome"] == "reserved"
    assert target.snapshot["reconciliation_action"] is None
    assert len(str(target.snapshot["precondition_digest"])) == 64

    result = execute_registered_action(
        db_session,
        definition=definition,
        target_id=str(receipt.id),
        target_revision=1,
        expected_target_snapshot=target.snapshot,
        payload={},
        actor_user_id=uuid.uuid4(),
    )

    assert result == {
        "changed": True,
        "receipt_id": str(receipt.id),
        "operation_id": str(receipt.operation_id),
        "reconciliation_action": expected_action,
        "reconciled_from_state": "reserved",
        "reconciled_from_io_outcome": "reserved",
        "state": expected_state,
        "io_outcome": expected_outcome,
        "retryable": action_key == _CONFIRM_ACTION,
        "new_revision": 2,
    }

    with pytest.raises(RegisteredActionTargetConflict, match="Only an unresolved"):
        inspect_registered_action_target(
            db_session,
            definition=definition,
            target_id=str(receipt.id),
            target_revision=2,
        )


def test_registry_reconciliation_rejects_stale_safe_and_missing_targets(db_session):
    definition = get_registered_action(_CONFIRM_ACTION)
    unsafe = _receipt()
    safe = _receipt(
        state="succeeded",
        io_outcome="response_received",
        retryable=False,
        settled_at=datetime.now(timezone.utc),
    )
    db_session.add_all([unsafe, safe])
    db_session.flush()

    with pytest.raises(RegisteredActionTargetConflict, match="changed"):
        inspect_registered_action_target(
            db_session,
            definition=definition,
            target_id=str(unsafe.id),
            target_revision=2,
        )
    with pytest.raises(RegisteredActionTargetConflict, match="Only an unresolved"):
        inspect_registered_action_target(
            db_session,
            definition=definition,
            target_id=str(safe.id),
            target_revision=1,
        )
    with pytest.raises(RegisteredActionTargetNotFound, match="not found"):
        inspect_registered_action_target(
            db_session,
            definition=definition,
            target_id=str(uuid.uuid4()),
            target_revision=1,
        )
