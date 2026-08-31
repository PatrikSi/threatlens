from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun


_OPERATION_NAMESPACE = uuid.UUID("00000000-0000-4000-8000-000000000302")
_OPERATION_ROOT_METADATA_KEY = "provider_operation_root_id"
_OPERATION_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SAFE_RETRY_OUTCOMES = frozenset({"not_sent", "response_received"})
_UNSAFE_STATES = frozenset({"reserved", "ambiguous"})


class AIProviderAttemptStateError(RuntimeError):
    """A durable receipt makes automatic provider I/O unsafe."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_ledger",
        requires_reconciliation: bool = False,
    ) -> None:
        self.category = category
        self.requires_reconciliation = requires_reconciliation
        super().__init__(message)


class AIProviderAttemptReconciliationError(AIProviderAttemptStateError):
    """An unresolved provider receipt cannot be reconciled as requested."""


class AIProviderTaskBindingError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AIProviderAttemptReservation:
    receipt_id: uuid.UUID
    operation_id: uuid.UUID
    attempt_number: int
    max_attempts: int
    reservation_generation: int
    resumed_safe_failure: bool
    next_max_tokens: int | None


def reserve_ai_provider_attempt(
    db: Session,
    *,
    task_run_id: uuid.UUID,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    operation_scope: str,
    attempt_number: int,
    max_attempts: int,
    requested_max_tokens: int,
    request_fingerprint: str,
    iam_revision: int,
    data_policy_revision: int,
    data_policy_mode: str,
) -> AIProviderAttemptReservation:
    if _OPERATION_SCOPE_PATTERN.fullmatch(operation_scope) is None:
        raise AIProviderTaskBindingError(
            "AI provider operation scope is invalid.", retryable=False
        )
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise AIProviderTaskBindingError(
            "AI provider task history is unavailable.", retryable=True
        )
    resource_type, resource_id = _validate_task_binding(
        run,
        feature_type=feature_type,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        report_id=report_id,
    )
    operation_root_id = _operation_root_id(
        db,
        run=run,
        feature_type=feature_type,
        resource_id=resource_id,
    )
    operation_id = uuid.uuid5(operation_root_id, operation_scope)
    _reject_unsafe_cross_operation_receipts(
        db,
        operation_id=operation_id,
        feature_type=feature_type,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    receipts = list(
        db.scalars(
            select(AIProviderAttemptReceipt)
            .where(AIProviderAttemptReceipt.operation_id == operation_id)
            .order_by(AIProviderAttemptReceipt.attempt_number)
            .with_for_update()
        ).all()
    )
    durable_max_attempts = _validate_receipt_ledger(
        receipts,
        proposed_max_attempts=max_attempts,
    )
    requested_attempt = max(1, int(attempt_number))
    existing = next(
        (
            receipt
            for receipt in receipts
            if receipt.attempt_number == requested_attempt
        ),
        None,
    )
    if existing is not None:
        _validate_existing_request(
            existing,
            request_fingerprint=request_fingerprint,
            requested_max_tokens=requested_max_tokens,
        )
        if existing.state == "voided":
            existing.state = "reserved"
            existing.io_outcome = "reserved"
            existing.retryable = None
            existing.next_max_tokens = None
            existing.settled_at = None
            existing.reservation_generation += 1
            existing.iam_revision = iam_revision
            existing.data_policy_revision = data_policy_revision
            existing.data_policy_mode = data_policy_mode
            existing.revision += 1
            db.add(existing)
            db.flush()
            return AIProviderAttemptReservation(
                receipt_id=existing.id,
                operation_id=operation_id,
                attempt_number=requested_attempt,
                max_attempts=durable_max_attempts,
                reservation_generation=existing.reservation_generation,
                resumed_safe_failure=False,
                next_max_tokens=None,
            )
        if (
            existing.state == "failed"
            and existing.retryable is True
            and existing.io_outcome in _SAFE_RETRY_OUTCOMES
            and existing.next_max_tokens is not None
            and requested_attempt < durable_max_attempts
        ):
            return AIProviderAttemptReservation(
                receipt_id=existing.id,
                operation_id=operation_id,
                attempt_number=requested_attempt,
                max_attempts=durable_max_attempts,
                reservation_generation=existing.reservation_generation,
                resumed_safe_failure=True,
                next_max_tokens=existing.next_max_tokens,
            )
        raise _existing_receipt_state_error(existing)

    expected_attempt = len(receipts) + 1
    if requested_attempt != expected_attempt:
        raise AIProviderAttemptStateError(
            "AI provider attempt history is incomplete or out of order."
        )
    if requested_attempt > durable_max_attempts:
        raise AIProviderAttemptStateError(
            "The durable AI provider attempt budget is exhausted."
        )
    if receipts and not _is_safe_retryable_failure(receipts[-1]):
        raise _existing_receipt_state_error(receipts[-1])

    receipt = AIProviderAttemptReceipt(
        operation_id=operation_id,
        attempt_number=requested_attempt,
        request_fingerprint=request_fingerprint,
        task_run_id_snapshot=run.id,
        feature_type=feature_type,
        resource_type=resource_type,
        resource_id=resource_id,
        max_attempts=durable_max_attempts,
        requested_max_tokens=max(1, int(requested_max_tokens)),
        reservation_generation=1,
        pre_io_failure_count=0,
        last_pre_io_failure_at=None,
        iam_revision=iam_revision,
        data_policy_revision=data_policy_revision,
        data_policy_mode=data_policy_mode,
        state="reserved",
        io_outcome="reserved",
        retryable=None,
        next_max_tokens=None,
        settled_at=None,
    )
    db.add(receipt)
    db.flush()
    return AIProviderAttemptReservation(
        receipt_id=receipt.id,
        operation_id=operation_id,
        attempt_number=requested_attempt,
        max_attempts=durable_max_attempts,
        reservation_generation=receipt.reservation_generation,
        resumed_safe_failure=False,
        next_max_tokens=None,
    )


def settle_ai_provider_attempt(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    request_fingerprint: str,
    state: str,
    io_outcome: str,
    retryable: bool,
    reservation_generation: int,
    next_max_tokens: int | None = None,
) -> AIProviderAttemptReceipt:
    receipt = db.scalar(
        select(AIProviderAttemptReceipt)
        .where(AIProviderAttemptReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise AIProviderAttemptStateError(
            "AI provider attempt receipt disappeared before settlement."
        )
    if receipt.request_fingerprint != request_fingerprint:
        raise AIProviderAttemptStateError(
            "AI provider attempt settlement does not match its reservation."
        )
    provided_generation = int(reservation_generation)
    if provided_generation < 1 or receipt.reservation_generation != provided_generation:
        raise AIProviderAttemptStateError(
            "AI provider attempt settlement belongs to an obsolete reservation generation.",
            category="stale_generation",
        )
    if receipt.state != "reserved" or receipt.io_outcome != "reserved":
        raise _existing_receipt_state_error(receipt)
    if state not in {"failed", "succeeded", "ambiguous"}:
        raise ValueError("Unsupported AI provider receipt state.")
    if io_outcome not in {"not_sent", "response_received", "ambiguous"}:
        raise ValueError("Unsupported AI provider I/O outcome.")
    if state == "succeeded" and (
        io_outcome != "response_received" or retryable or next_max_tokens is not None
    ):
        raise ValueError("Successful provider receipts require a received response.")
    if state == "ambiguous" and (
        io_outcome != "ambiguous" or retryable or next_max_tokens is not None
    ):
        raise ValueError("Ambiguous provider receipts cannot be retryable.")
    if state == "failed" and (
        io_outcome not in _SAFE_RETRY_OUTCOMES
        or (retryable and next_max_tokens is None)
        or (not retryable and next_max_tokens is not None)
    ):
        raise ValueError("Failed provider receipt settlement is inconsistent.")

    receipt.state = state
    receipt.io_outcome = io_outcome
    receipt.retryable = bool(retryable)
    receipt.next_max_tokens = (
        max(1, int(next_max_tokens)) if next_max_tokens is not None else None
    )
    receipt.settled_at = datetime.now(timezone.utc)
    receipt.revision += 1
    db.add(receipt)
    db.flush()
    return receipt


def void_ai_provider_attempt_reservation(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    request_fingerprint: str,
    reservation_generation: int,
) -> AIProviderAttemptReceipt:
    receipt = db.scalar(
        select(AIProviderAttemptReceipt)
        .where(AIProviderAttemptReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise AIProviderAttemptStateError(
            "AI provider attempt receipt disappeared before its reservation was voided.",
            category="missing_receipt",
        )
    if receipt.request_fingerprint != request_fingerprint:
        raise AIProviderAttemptStateError(
            "AI provider attempt void does not match its reservation.",
            category="request_changed",
        )
    provided_generation = int(reservation_generation)
    if provided_generation < 1 or receipt.reservation_generation != provided_generation:
        raise AIProviderAttemptStateError(
            "AI provider attempt void belongs to an obsolete reservation generation.",
            category="stale_generation",
        )
    if receipt.state != "reserved" or receipt.io_outcome != "reserved":
        raise _existing_receipt_state_error(receipt)

    now = datetime.now(timezone.utc)
    receipt.state = "voided"
    receipt.io_outcome = "not_sent"
    receipt.retryable = True
    receipt.next_max_tokens = None
    receipt.settled_at = now
    receipt.pre_io_failure_count += 1
    receipt.last_pre_io_failure_at = now
    receipt.revision += 1
    db.add(receipt)
    db.flush()
    return receipt


def reconcile_ai_provider_attempt(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    expected_revision: int,
    action: str,
    actor_user_id: uuid.UUID,
) -> AIProviderAttemptReceipt:
    receipt = db.scalar(
        select(AIProviderAttemptReceipt)
        .where(AIProviderAttemptReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise AIProviderAttemptReconciliationError(
            "AI provider attempt receipt was not found."
        )
    provided_revision = int(expected_revision)
    if provided_revision < 1 or receipt.revision != provided_revision:
        raise AIProviderAttemptReconciliationError(
            "AI provider attempt receipt changed after it was reviewed."
        )
    if (
        receipt.reconciliation_action is not None
        or receipt.state not in _UNSAFE_STATES
        or receipt.io_outcome not in {"reserved", "ambiguous"}
    ):
        raise AIProviderAttemptReconciliationError(
            "Only an unresolved reserved or ambiguous provider attempt can be reconciled."
        )
    if action not in {"confirmed_not_sent", "acknowledged_may_have_sent"}:
        raise AIProviderAttemptReconciliationError(
            "AI provider attempt reconciliation action is invalid."
        )

    now = datetime.now(timezone.utc)
    receipt.reconciled_from_state = receipt.state
    receipt.reconciled_from_io_outcome = receipt.io_outcome
    receipt.reconciliation_action = action
    receipt.reconciled_by_user_id_snapshot = actor_user_id
    receipt.reconciled_at = now
    receipt.state = "failed"
    if action == "confirmed_not_sent":
        receipt.io_outcome = "not_sent"
        receipt.retryable = receipt.attempt_number < receipt.max_attempts
        receipt.next_max_tokens = (
            receipt.requested_max_tokens if receipt.retryable else None
        )
    else:
        receipt.state = "ambiguous"
        receipt.io_outcome = "ambiguous"
        receipt.retryable = False
        receipt.next_max_tokens = None
    receipt.settled_at = receipt.settled_at or now
    receipt.revision += 1
    db.add(receipt)
    db.flush()
    return receipt


def lock_ai_provider_attempt_for_io(
    db: Session,
    *,
    reservation: AIProviderAttemptReservation,
    task_run_id: uuid.UUID,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    request_fingerprint: str,
) -> AIProviderAttemptReceipt:
    """Lock the exact active task and reserved receipt through one provider call."""

    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == task_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise AIProviderTaskBindingError(
            "AI provider task history is unavailable.", retryable=True
        )
    resource_type, resource_id = _validate_task_binding(
        run,
        feature_type=feature_type,
        item_id=item_id,
        daily_brief_id=daily_brief_id,
        report_id=report_id,
    )
    receipt = db.scalar(
        select(AIProviderAttemptReceipt)
        .where(AIProviderAttemptReceipt.id == reservation.receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        receipt is None
        or receipt.operation_id != reservation.operation_id
        or receipt.attempt_number != reservation.attempt_number
        or receipt.reservation_generation != reservation.reservation_generation
        or receipt.task_run_id_snapshot != run.id
        or receipt.feature_type != feature_type
        or receipt.resource_type != resource_type
        or receipt.resource_id != resource_id
        or receipt.request_fingerprint != request_fingerprint
        or receipt.state != "reserved"
        or receipt.io_outcome != "reserved"
        or receipt.reconciliation_action is not None
    ):
        raise AIProviderAttemptStateError(
            "The durable AI provider reservation changed before external I/O."
        )
    return receipt


def _validate_task_binding(
    run: AITaskRun,
    *,
    feature_type: str,
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
) -> tuple[str, uuid.UUID | None]:
    if (
        run.status != "running"
        or run.finished_at is not None
        or run.superseded_by_task_run_id is not None
        or _task_stop_requested(run)
    ):
        raise AIProviderTaskBindingError(
            "AI provider task is not an active, uncancelled run.", retryable=False
        )
    expected_resources = {
        "item_enrichment": ("item", item_id),
        "daily_brief": ("ai_daily_brief", daily_brief_id),
        "report": ("report", report_id),
        "connection_test": ("connection_test", None),
    }
    expected = expected_resources.get(feature_type)
    if expected is None or run.task_type != feature_type:
        raise AIProviderTaskBindingError(
            "AI provider task type does not match the requested feature.",
            retryable=False,
        )
    if feature_type != "connection_test" and expected[1] is None:
        raise AIProviderTaskBindingError(
            "AI provider task resource is unavailable.", retryable=False
        )
    expected_item_id = item_id if feature_type == "item_enrichment" else None
    expected_brief_id = daily_brief_id if feature_type == "daily_brief" else None
    expected_report_id = report_id if feature_type == "report" else None
    if (
        run.item_id != expected_item_id
        or run.daily_brief_id != expected_brief_id
        or run.report_id != expected_report_id
    ):
        raise AIProviderTaskBindingError(
            "AI provider task resource does not match the governed request.",
            retryable=False,
        )
    return expected


def _operation_root_id(
    db: Session,
    *,
    run: AITaskRun,
    feature_type: str,
    resource_id: uuid.UUID | None,
) -> uuid.UUID:
    metadata = dict(run.metadata_json or {})
    stored = _uuid_or_none(metadata.get(_OPERATION_ROOT_METADATA_KEY))
    if stored is not None:
        return stored
    if run.parent_run_id is not None:
        root_id = uuid.uuid5(
            _OPERATION_NAMESPACE,
            f"parent:{run.parent_run_id}:{feature_type}:{resource_id}",
        )
    else:
        anchor_id = _supersession_root_id(db, run)
        root_id = uuid.uuid5(_OPERATION_NAMESPACE, f"task:{anchor_id}")
    metadata[_OPERATION_ROOT_METADATA_KEY] = str(root_id)
    run.metadata_json = metadata
    db.add(run)
    return root_id


def _supersession_root_id(db: Session, run: AITaskRun) -> uuid.UUID:
    current = run
    observed = {run.id}
    for _depth in range(16):
        previous_id = _uuid_or_none(
            (current.metadata_json or {}).get("supersedes_task_run_id")
        )
        if previous_id is None or previous_id in observed:
            return current.id
        previous = db.get(AITaskRun, previous_id)
        if previous is None:
            return previous_id
        observed.add(previous_id)
        current = previous
    raise AIProviderAttemptStateError("AI task supersession lineage is invalid.")


def _reject_unsafe_cross_operation_receipts(
    db: Session,
    *,
    operation_id: uuid.UUID,
    feature_type: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
) -> None:
    if resource_id is None:
        return
    unsafe = db.scalar(
        select(AIProviderAttemptReceipt.id)
        .where(
            AIProviderAttemptReceipt.operation_id != operation_id,
            AIProviderAttemptReceipt.feature_type == feature_type,
            AIProviderAttemptReceipt.resource_type == resource_type,
            AIProviderAttemptReceipt.resource_id == resource_id,
            AIProviderAttemptReceipt.state.in_(_UNSAFE_STATES),
            AIProviderAttemptReceipt.reconciliation_action.is_(None),
        )
        .limit(1)
    )
    if unsafe is not None:
        raise AIProviderAttemptStateError(
            "Another logical operation has an unresolved AI provider attempt that "
            "requires two-person reconciliation.",
            category="reconciliation_required",
            requires_reconciliation=True,
        )


def _validate_receipt_ledger(
    receipts: list[AIProviderAttemptReceipt],
    *,
    proposed_max_attempts: int,
) -> int:
    if not receipts:
        return max(1, int(proposed_max_attempts))
    durable_max_attempts = int(receipts[0].max_attempts)
    for expected_attempt, receipt in enumerate(receipts, start=1):
        if (
            receipt.attempt_number != expected_attempt
            or receipt.max_attempts != durable_max_attempts
        ):
            raise AIProviderAttemptStateError(
                "AI provider attempt ledger is inconsistent."
            )
    return durable_max_attempts


def _validate_existing_request(
    receipt: AIProviderAttemptReceipt,
    *,
    request_fingerprint: str,
    requested_max_tokens: int,
) -> None:
    if (
        receipt.request_fingerprint != request_fingerprint
        or receipt.requested_max_tokens != max(1, int(requested_max_tokens))
    ):
        if (
            receipt.reconciliation_action is None
            and receipt.state in _UNSAFE_STATES
        ):
            raise AIProviderAttemptStateError(
                "The prepared AI request changed while its prior provider receipt "
                "remains unresolved. Reconcile the reserved or ambiguous receipt "
                "before starting a new logical operation.",
                category="reconciliation_required",
                requires_reconciliation=True,
            )
        raise AIProviderAttemptStateError(
            "The prepared AI request changed after its durable reservation. Start "
            "a new task or logical operation.",
            category="request_changed",
        )


def _existing_receipt_state_error(
    receipt: AIProviderAttemptReceipt,
) -> AIProviderAttemptStateError:
    if receipt.reconciliation_action is not None:
        return AIProviderAttemptStateError(
            "This AI provider attempt was already reconciled and is terminal for "
            "the current logical operation. Start a new task or logical operation.",
            category="reconciled_terminal",
        )
    if receipt.state in _UNSAFE_STATES:
        return AIProviderAttemptStateError(
            "This AI provider attempt is reserved or ambiguous and requires "
            "two-person reconciliation before any new provider request.",
            category="reconciliation_required",
            requires_reconciliation=True,
        )
    if receipt.state == "succeeded":
        return AIProviderAttemptStateError(
            "This AI provider attempt already succeeded. Start a new task or "
            "logical operation instead of replaying it.",
            category="already_succeeded",
        )
    if receipt.state == "failed":
        return AIProviderAttemptStateError(
            "This AI provider attempt is terminal or its durable retry budget is "
            "exhausted. Start a new task or logical operation.",
            category="terminal_failed",
        )
    if receipt.state == "voided":
        return AIProviderAttemptStateError(
            "This AI provider reservation was already voided before provider I/O.",
            category="already_voided",
        )
    return AIProviderAttemptStateError(
        "The AI provider attempt ledger is inconsistent.",
        category="invalid_ledger",
    )


def _is_safe_retryable_failure(receipt: AIProviderAttemptReceipt) -> bool:
    return (
        receipt.state == "failed"
        and receipt.retryable is True
        and receipt.io_outcome in _SAFE_RETRY_OUTCOMES
        and receipt.next_max_tokens is not None
    )


def _task_stop_requested(run: AITaskRun) -> bool:
    metadata = run.metadata_json or {}
    return bool(metadata.get("cancel_requested_at")) or run.reason in {
        "cancel_requested",
        "canceled",
    }


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "AIProviderAttemptReconciliationError",
    "AIProviderAttemptReservation",
    "AIProviderAttemptStateError",
    "AIProviderTaskBindingError",
    "lock_ai_provider_attempt_for_io",
    "reconcile_ai_provider_attempt",
    "reserve_ai_provider_attempt",
    "settle_ai_provider_attempt",
    "void_ai_provider_attempt_reservation",
]
