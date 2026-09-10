"""Drain expired history without crossing a transaction's child-row budget.

Callers supply their full retention predicate. Parent locks and database write
barriers prevent newly retained references after a pruning claim is committed.
Permissions and provider-side-effect receipts are never removed by this helper.
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_task_event import AITaskEvent
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import AlertEvaluationRequestActivity
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.integration import IntegrationAttempt
from app.models.lifecycle_pruning import LifecyclePruningRecord

from app.services.lifecycle_pruning_contracts import PruningContext, PruningResult


_CHILDREN = {
    "ai_task_runs": ((AITaskEvent, AITaskEvent.task_run_id),),
    "integration_deliveries": ((IntegrationAttempt, IntegrationAttempt.delivery_id),),
    "alert_evaluation_requests": (
        (AlertEvaluationRequestActivity, AlertEvaluationRequestActivity.request_id),
        (AlertEvaluationMatch, AlertEvaluationMatch.request_id),
    ),
    "action_approval_requests": ((GovernanceOperationReceipt, GovernanceOperationReceipt.resource_id),),
}


def supports_incremental_pruning(model: type) -> bool:
    return model.__table__.name in _CHILDREN


def prune_oversized_parent(
    db: Session, *, model: type, parent_id: uuid.UUID,
    context: PruningContext, limit: int,
) -> PruningResult:
    dataset = model.__table__.name
    children = _CHILDREN.get(dataset)
    if not children or limit <= 0:
        return PruningResult()
    # Recheck cross-table pins after obtaining the source lock. Each new-reference
    # database trigger takes this same source lock before checking the claim.
    found = db.scalar(select(model.id).where(model.id == parent_id).with_for_update())
    if found is None or db.scalar(select(model.id).where(
        model.id == parent_id, context.eligibility,
    )) is None:
        return PruningResult()
    if dataset == "ai_task_runs" and not lock_ai_history_receipts(db, [parent_id]):
        return PruningResult()
    # Receipt state may have changed while its row lock was acquired.
    if db.scalar(select(model.id).where(model.id == parent_id, context.eligibility)) is None:
        return PruningResult()
    record = db.get(LifecyclePruningRecord, (dataset, parent_id))
    started = record is None
    if record is None:
        record = LifecyclePruningRecord(dataset=dataset, parent_id=parent_id, cutoff_at=context.cutoff)
        db.add(record)
        db.flush([record])
    pruned = 0
    for child, parent_column in children:
        remaining = limit - pruned
        if remaining <= 0:
            break
        filters = [parent_column == parent_id]
        if child is GovernanceOperationReceipt:
            filters.append(GovernanceOperationReceipt.resource_type == "action_approval")
        child_ids = select(child.id).where(*filters).order_by(child.id).limit(remaining).with_for_update(skip_locked=True)
        result = db.execute(delete(child).where(child.id.in_(child_ids)).execution_options(synchronize_session=False))
        pruned += int(result.rowcount or 0)
    record.children_pruned += pruned
    record.updated_at = datetime.now(timezone.utc)
    db.flush([record])
    return PruningResult(children_pruned=pruned, parents_started=int(started))


def lock_ai_history_receipts(db: Session, parent_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Skip source parents with concurrently locked or excessive receipt history.

    Receipt mutation triggers may already own their child row before they try to
    fence its source. Waiting here would invert that order and deadlock cleanup.
    """
    if not parent_ids:
        return []
    receipts = list(db.execute(select(
        AIProviderAttemptReceipt.id, AIProviderAttemptReceipt.task_run_id_snapshot,
    ).where(AIProviderAttemptReceipt.task_run_id_snapshot.in_(parent_ids)).limit(10_001)))
    if len(receipts) > 10_000:
        return []
    locked = set(db.scalars(select(AIProviderAttemptReceipt.id).where(
        AIProviderAttemptReceipt.id.in_([row.id for row in receipts]),
    ).with_for_update(skip_locked=True))) if receipts else set()
    blocked = {row.task_run_id_snapshot for row in receipts if row.id not in locked}
    return [parent_id for parent_id in parent_ids if parent_id not in blocked]
