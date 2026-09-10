"""Drain expired history without crossing a transaction's child-row budget.

Callers supply their full retention predicate. Parent locks and database write
barriers prevent newly retained references after a pruning claim is committed.
Permissions and provider-side-effect receipts are never removed by this helper.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.action_approval import ActionExecutionReceipt
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import AlertEvaluationRequestActivity
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.integration import IntegrationAttempt, IntegrationDelivery
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report import Report
from app.models.lifecycle_pruning import LifecyclePruningRecord

from app.services.lifecycle_pruning_contracts import PruningContext, PruningResult
from app.services.lifecycle_permission_pruning import (
    lock_permission_history_dependants,
    permission_pruning_candidates,
    prune_permission_history_parent,
    supports_permission_pruning,
)


_CHILDREN = {
    "ai_task_runs": ((AITaskEvent, AITaskEvent.task_run_id),),
    "integration_deliveries": ((IntegrationAttempt, IntegrationAttempt.delivery_id),),
    "alert_evaluation_requests": (
        (AlertEvaluationRequestActivity, AlertEvaluationRequestActivity.request_id),
        (AlertEvaluationMatch, AlertEvaluationMatch.request_id),
    ),
    "action_approval_requests": (
        (GovernanceOperationReceipt, GovernanceOperationReceipt.resource_id),
    ),
}


_RETAINED_REFERENCES = {
    "ai_task_runs": (
        AITaskRun.parent_run_id,
        AITaskRun.superseded_by_task_run_id,
        Report.request_task_run_id,
    ),
    "integration_deliveries": (
        IntegrationDelivery.source_delivery_id,
        NotificationWebhookDelivery.integration_delivery_id,
    ),
    "action_approval_requests": (ActionExecutionReceipt.approval_request_id,),
}


def incremental_pruning_candidates(
    db: Session,
    *,
    model: type,
    parent_ids: list[uuid.UUID],
    max_dependent_rows: int,
) -> set[uuid.UUID]:
    """Do not strand a claim on a bundle dominated by retained references."""
    if supports_permission_pruning(model):
        return permission_pruning_candidates(
            db,
            model=model,
            parent_ids=parent_ids,
            max_dependent_rows=max_dependent_rows,
        )
    children = _CHILDREN.get(model.__table__.name)
    if not parent_ids or not children or max_dependent_rows <= 0:
        return set()
    counts = []
    for reference in _RETAINED_REFERENCES.get(model.__table__.name, ()):
        child = aliased(reference.class_)
        column = getattr(child, reference.key)
        limited = (
            select(literal(1))
            .where(column == model.id)
            .limit(max_dependent_rows + 1)
            .correlate(model)
            .subquery()
        )
        counts.append(select(func.count()).select_from(limited).scalar_subquery())
    has_children = []
    for child, column in children:
        predicate = column == model.id
        if child is GovernanceOperationReceipt:
            predicate = predicate & (
                GovernanceOperationReceipt.resource_type == "action_approval"
            )
        has_children.append(select(child.id).where(predicate).exists())
    rows = db.execute(
        select(model.id, *counts).where(model.id.in_(parent_ids), or_(*has_children))
    )
    return {
        row[0]
        for row in rows
        if sum(int(count) for count in row[1:]) <= max_dependent_rows
    }


def supports_incremental_pruning(model: type) -> bool:
    return model.__table__.name in _CHILDREN or supports_permission_pruning(model)


def prune_oversized_parent(
    db: Session,
    *,
    model: type,
    parent_id: uuid.UUID,
    context: PruningContext,
    limit: int,
) -> PruningResult:
    if supports_permission_pruning(model):
        return prune_permission_history_parent(
            db,
            model=model,
            parent_id=parent_id,
            context=context,
            limit=limit,
        )
    dataset = model.__table__.name
    children = _CHILDREN.get(dataset)
    if not children or limit <= 0:
        return PruningResult()
    # Recheck cross-table pins after obtaining the source lock. Each new-reference
    # database trigger takes this same source lock before checking the claim.
    found = db.scalar(select(model.id).where(model.id == parent_id).with_for_update())
    if (
        found is None
        or db.scalar(
            select(model.id).where(
                model.id == parent_id,
                context.eligibility,
            )
        )
        is None
    ):
        return PruningResult()
    if parent_id not in incremental_pruning_candidates(
        db,
        model=model,
        parent_ids=[parent_id],
        max_dependent_rows=context.parent_row_budget,
    ):
        return PruningResult()
    if dataset == "ai_task_runs" and not lock_ai_history_receipts(db, [parent_id]):
        return PruningResult()
    # Receipt state may have changed while its row lock was acquired.
    if (
        db.scalar(select(model.id).where(model.id == parent_id, context.eligibility))
        is None
    ):
        return PruningResult()
    record = db.get(LifecyclePruningRecord, (dataset, parent_id))
    started = record is None
    if record is None:
        record = LifecyclePruningRecord(
            dataset=dataset, parent_id=parent_id, cutoff_at=context.cutoff
        )
        db.add(record)
        db.flush([record])
    pruned = 0
    for child, parent_column in children:
        remaining = limit - pruned
        if remaining <= 0:
            break
        filters = [parent_column == parent_id]
        if child is GovernanceOperationReceipt:
            filters.append(
                GovernanceOperationReceipt.resource_type == "action_approval"
            )
        child_ids = (
            select(child.id)
            .where(*filters)
            .order_by(child.id)
            .limit(remaining)
            .with_for_update(skip_locked=True)
        )
        result = db.execute(
            delete(child)
            .where(child.id.in_(child_ids))
            .execution_options(synchronize_session=False)
        )
        pruned += int(result.rowcount or 0)
    record.children_pruned += pruned
    record.updated_at = datetime.now(timezone.utc)
    db.flush([record])
    return PruningResult(children_pruned=pruned, parents_started=int(started))


def lock_history_dependants(
    db: Session, *, model: type, parent_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Skip a parent when a child writer already holds one of its references.

    Callers must already own the parent rows. Reference triggers close new-write
    races, but waiting on an existing child would invert their lock order. Read
    reference IDs in bounded pages so legacy maintenance calls remain bounded in
    memory even when their transaction has no explicit dependent-row budget.
    """
    if supports_permission_pruning(model):
        return lock_permission_history_dependants(
            db, model=model, parent_ids=parent_ids
        )
    if not parent_ids:
        return []
    references = [column for _, column in _CHILDREN.get(model.__table__.name, ())]
    references.extend(_RETAINED_REFERENCES.get(model.__table__.name, ()))
    blocked: set[uuid.UUID] = set()
    for column in references:
        child = column.class_
        filters = [column.in_(parent_ids)]
        if child is GovernanceOperationReceipt:
            filters.append(child.resource_type == "action_approval")
        anchor = None
        while True:
            query = (
                select(child.id, column).where(*filters).order_by(child.id).limit(1_000)
            )
            if anchor is not None:
                query = query.where(child.id > anchor)
            rows = list(db.execute(query))
            if not rows:
                break
            locked = set(
                db.scalars(
                    select(child.id)
                    .where(
                        child.id.in_([row[0] for row in rows]),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            blocked.update(row[1] for row in rows if row[0] not in locked)
            anchor = rows[-1][0]
    return [parent_id for parent_id in parent_ids if parent_id not in blocked]


def lock_ai_history_receipts(
    db: Session, parent_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Skip source parents with concurrently locked or excessive receipt history.

    Receipt mutation triggers may already own their child row before they try to
    fence its source. Waiting here would invert that order and deadlock cleanup.
    """
    if not parent_ids:
        return []
    receipts = list(
        db.execute(
            select(
                AIProviderAttemptReceipt.id,
                AIProviderAttemptReceipt.task_run_id_snapshot,
            )
            .where(AIProviderAttemptReceipt.task_run_id_snapshot.in_(parent_ids))
            .limit(10_001)
        )
    )
    if len(receipts) > 10_000:
        return []
    locked = (
        set(
            db.scalars(
                select(AIProviderAttemptReceipt.id)
                .where(
                    AIProviderAttemptReceipt.id.in_([row.id for row in receipts]),
                )
                .with_for_update(skip_locked=True)
            )
        )
        if receipts
        else set()
    )
    blocked = {row.task_run_id_snapshot for row in receipts if row.id not in locked}
    return [parent_id for parent_id in parent_ids if parent_id not in blocked]
