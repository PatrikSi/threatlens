from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt


def test_ai_provider_attempt_receipt_model_matches_durable_history_contract():
    table = AIProviderAttemptReceipt.__table__

    assert set(table.columns) == {
        table.c.id,
        table.c.operation_id,
        table.c.attempt_number,
        table.c.request_fingerprint,
        table.c.task_run_id_snapshot,
        table.c.feature_type,
        table.c.resource_type,
        table.c.resource_id,
        table.c.max_attempts,
        table.c.requested_max_tokens,
        table.c.reservation_generation,
        table.c.pre_io_failure_count,
        table.c.last_pre_io_failure_at,
        table.c.revision,
        table.c.iam_revision,
        table.c.data_policy_revision,
        table.c.data_policy_mode,
        table.c.state,
        table.c.io_outcome,
        table.c.retryable,
        table.c.next_max_tokens,
        table.c.created_at,
        table.c.settled_at,
        table.c.reconciliation_action,
        table.c.reconciled_from_state,
        table.c.reconciled_from_io_outcome,
        table.c.reconciled_by_user_id_snapshot,
        table.c.reconciled_at,
        table.c.updated_at,
    }
    assert not table.foreign_keys
    assert table.c.task_run_id_snapshot.nullable is False
    assert table.c.resource_id.nullable is True
    assert table.c.iam_revision.nullable is False
    assert table.c.data_policy_revision.nullable is False
    assert table.c.state.server_default.arg == "reserved"
    assert table.c.io_outcome.server_default.arg == "reserved"
    assert table.c.revision.server_default.arg == "1"
    assert table.c.reservation_generation.server_default.arg == "1"
    assert table.c.pre_io_failure_count.server_default.arg == "0"

    checks = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_ai_provider_attempt_receipts_attempt_bounds",
        "ck_ai_provider_attempt_receipts_token_bounds",
        "ck_ai_provider_attempt_receipts_iam_revision",
        "ck_ai_provider_attempt_receipts_revision",
        "ck_ai_provider_attempt_receipts_reservation_generation",
        "ck_ai_provider_attempt_receipts_pre_io_failures",
        "ck_ai_provider_attempt_receipts_policy_revision",
        "ck_ai_provider_attempt_receipts_fingerprint",
        "ck_ai_provider_attempt_receipts_policy_mode",
        "ck_ai_provider_attempt_receipts_state",
        "ck_ai_provider_attempt_receipts_io_outcome",
        "ck_ai_provider_attempt_receipts_lifecycle",
        "ck_ai_provider_attempt_receipts_reconciliation",
    }
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques == {
        "uq_ai_provider_attempt_receipts_operation_attempt": (
            "operation_id",
            "attempt_number",
        )
    }
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert indexes == {
        "ix_ai_provider_attempt_receipts_resource": (
            "resource_type",
            "resource_id",
        ),
        "ix_ai_provider_attempt_receipts_task_run_snapshot": (
            "task_run_id_snapshot",
        ),
    }
