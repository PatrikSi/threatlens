"""Idempotent, monotonic receiver status with separately acknowledged withdrawals."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.automation_execution import (
    AutomationCallback,
    AutomationExecution,
    AutomationPolicyUpdate,
)
from app.models.integration import IntegrationEvent
from app.schemas.automation_execution import ExecutionCallback

TRACKED_EVENTS = frozenset(
    {"intel.extraction.ready", "intel.indicators.changed", "hunt.approved"}
)
TERMINAL_STATUSES = frozenset({"completed", "failed"})


def register_execution(
    db: Session, *, event: IntegrationEvent, webhook
) -> AutomationExecution | None:
    if (
        event.event_type not in TRACKED_EVENTS
        or webhook.payload_mode != "automation_v1"
    ):
        return None
    action_id = str(event.payload_json.get("action_id") or event.id)
    db.execute(
        insert(AutomationExecution)
        .values(
            id=uuid.uuid4(),
            webhook_id=webhook.id,
            owner_user_id=webhook.user_id,
            event_id=event.id,
            action_id=action_id,
        )
        .on_conflict_do_nothing(index_elements=["webhook_id", "action_id"])
    )
    row = db.scalar(
        select(AutomationExecution).where(
            AutomationExecution.webhook_id == webhook.id,
            AutomationExecution.action_id == action_id,
        )
    )

    if row.event_id != event.id:
        raise ValueError("Automation action ID was reused for a different event")
    return row


def apply_callback(
    db: Session, row: AutomationExecution, payload: ExecutionCallback
) -> bool:
    """Caller holds current credential/source authorization and the execution lock."""
    digest = hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    receipt = db.get(AutomationCallback, (row.id, payload.callback_id))
    if receipt is not None:
        if receipt.digest != digest:
            raise HTTPException(
                409, "Callback ID was already used for different content"
            )
        return False
    if payload.sequence <= row.sequence:
        raise HTTPException(
            409, "Callback sequence is older than the stored receiver status"
        )
    if (
        row.external_job_id is not None
        and row.external_job_id != payload.external_job_id
    ):
        raise HTTPException(409, "External job ID cannot change for an accepted action")
    if row.status in TERMINAL_STATUSES:
        raise HTTPException(
            409,
            "Completed or failed executions are immutable; replay the original callback for acknowledgement",
        )
    if row.progress_stage >= 2 and payload.status == "accepted":
        raise HTTPException(409, "Running executions cannot return to accepted")
    if payload.status != "completed" and payload.findings is not None:
        raise HTTPException(
            422, "Findings can be recorded only with completed execution status"
        )
    row.external_job_id, row.sequence = payload.external_job_id, payload.sequence
    row.progress_stage = max(
        row.progress_stage,
        {"unknown": 0, "accepted": 1, "running": 2, "completed": 3, "failed": 3}[
            payload.status
        ],
    )
    row.status, row.findings = payload.status, payload.findings
    row.updated_at = datetime.now(timezone.utc)
    db.add(
        AutomationCallback(
            execution_id=row.id,
            callback_id=payload.callback_id,
            sequence=payload.sequence,
            digest=digest,
        )
    )
    return True


def reserve_policy_update(
    db: Session,
    row: AutomationExecution,
    *,
    kind: str = "intel.withdrawn",
    reason: str,
    replacement_action_id: str | None = None,
) -> AutomationPolicyUpdate:
    """The locked execution is the per-destination revision ledger, never a job reset."""
    if kind not in {"intel.withdrawn", "intel.replaced"}:
        raise ValueError("Unsupported policy update")
    row.policy_revision += 1
    row.policy_state = "withdrawn" if kind == "intel.withdrawn" else "replaced"
    update = AutomationPolicyUpdate(
        execution_id=row.id,
        revision=row.policy_revision,
        event_type=kind,
        reason=reason,
        replacement_action_id=replacement_action_id,
    )
    db.add(update)
    return update


def reconcile_executions(
    db: Session, *, limit: int = 100, owner_user_id: uuid.UUID | None = None
) -> int:
    """Bounded fair scan persists the next check; receiver failures cannot block it.

    A withdrawn action is never automatically reactivated. A fresh approved event
    owns a fresh action ID and can explicitly replace it after normal routing.
    """
    from app.services.intel_event_eligibility import (
        automation_event_current,
        IntelEventBusy,
    )

    now = datetime.now(timezone.utc)
    query = select(AutomationExecution).where(
        AutomationExecution.next_check_at <= now,
        AutomationExecution.policy_state == "current",
    )
    if owner_user_id is not None:
        query = query.where(AutomationExecution.owner_user_id == owner_user_id)
    rows = db.scalars(
        query.order_by(AutomationExecution.next_check_at, AutomationExecution.id)
        .limit(min(max(limit, 1), 100))
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        event = db.get(IntegrationEvent, row.event_id)
        row.next_check_at = now + timedelta(minutes=5)
        try:
            current = event is not None and automation_event_current(
                db, event.payload_json, event.event_type
            )
        except IntelEventBusy:
            row.next_check_at = now + timedelta(seconds=30)
            continue
        if not current:
            replacement = _replacement_action(db, row, event)
            reserve_policy_update(
                db,
                row,
                kind="intel.replaced" if replacement else "intel.withdrawn",
                replacement_action_id=replacement,
                reason="Source, approval, indicator verdict, expiry, or suppression changed; stop using the previous action",
            )
    return len(rows)


def _replacement_action(
    db: Session, row: AutomationExecution, event: IntegrationEvent | None
) -> str | None:
    """Only a separately routed, current action at this same destination replaces it."""
    if event is None:
        return None
    from app.services.intel_event_eligibility import automation_event_current

    query = (
        select(AutomationExecution, IntegrationEvent)
        .join(
            IntegrationEvent,
            IntegrationEvent.id == AutomationExecution.event_id,
        )
        .where(
            AutomationExecution.webhook_id == row.webhook_id,
            AutomationExecution.id != row.id,
            AutomationExecution.policy_state == "current",
            IntegrationEvent.source_type == event.source_type,
            IntegrationEvent.source_id == event.source_id,
            IntegrationEvent.event_type == event.event_type,
            IntegrationEvent.created_at > event.created_at,
        )
    )
    for candidate, candidate_event in db.execute(
        query.order_by(IntegrationEvent.created_at.desc()).limit(10)
    ):
        payload = candidate_event.payload_json
        if any(
            payload.get(key) != event.payload_json.get(key)
            for key in ("team_id", "hunt_id")
        ):
            continue
        if automation_event_current(db, payload, candidate_event.event_type):
            return candidate.action_id
    return None


def tracked_action_delivery(delivery) -> bool:
    """The routing snapshot, not mutable webhook configuration, selects semantics."""
    return (
        delivery.connector_type == "webhook"
        and isinstance(delivery.payload_json, dict)
        and isinstance(delivery.payload_json.get("execution"), dict)
    )


def ambiguous_action_retry(db: Session, delivery) -> bool:
    if (
        delivery.status_code is not None
        or delivery.event_type_snapshot not in TRACKED_EVENTS
    ):
        return False
    from app.models.integration import IntegrationAttempt, IntegrationDelivery

    generic = (
        db.get(IntegrationDelivery, delivery.integration_delivery_id)
        if delivery.integration_delivery_id
        else None
    )
    if generic is None or not tracked_action_delivery(generic):
        return False
    attempt = db.scalar(
        select(IntegrationAttempt).where(
            IntegrationAttempt.delivery_id == generic.id,
            IntegrationAttempt.attempt_number == generic.attempt_count,
        )
    )
    receipt = (
        attempt.response_json
        if attempt and isinstance(attempt.response_json, dict)
        else {}
    )
    return receipt.get("external_side_effect_possible") is not False
