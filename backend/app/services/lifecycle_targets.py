from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.models.action_approval import ActionApprovalRequest
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceActivity,
    AlertOccurrenceMetric,
)
from app.models.article import Article
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.integration import (
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationEvent,
    IntegrationRun,
)
from app.models.investigation import InvestigationEvidence
from app.models.item import Item
from app.models.item_state import ItemState
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report_source_item import ReportSourceItem
from app.models.report import Report
from app.models.system_health_sample import SystemHealthSample
from app.models.tag import TagFeedbackEvent
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
)
from app.services.data_access_retention import prune_deleted_resource_envelopes
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
from app.services.history_maintenance import (
    _delete_action_approval_history,
    _delete_ai_history_with_envelopes,
    _delete_expired_ai_provider_receipt_ledgers,
    _eligible_ai_provider_receipt_operation_ids,
    _retained_action_approval_run_reference,
    _unresolved_ai_provider_receipt,
)
from app.services.integration_maintenance import (
    _integration_delivery_retention_predicates,
    _legacy_notification_delivery_retention_predicates,
    prune_integration_delivery_history,
)
from app.services.lifecycle_dependencies import (
    MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    has_lifecycle_dependants,
    lifecycle_parent_scan_limit,
    partition_oversized_lifecycle_parents,
    select_with_dependent_budget,
)
from app.services.alert_maintenance import maintain_alert_history


PREVIEW_COUNT_LIMIT = 10_000
DEPENDENCY_PREVIEW_PARENT_LIMIT = 1_000
_ALERT_RETENTION_DISABLED_DAYS = 365_000
ARTICLE_BATCH_RECORD_LIMIT = 100
ARTICLE_BATCH_BYTE_LIMIT = 32 * 1024 * 1024


@dataclass(frozen=True)
class TargetPreview:
    eligible_count: int
    protected_count: int = 0
    protected_counts: dict[str, int] = field(default_factory=dict)
    oldest_candidate_at: datetime | None = None
    count_is_lower_bound: bool = False
    is_partial: bool = False
    eligible_bytes: int | None = None


@dataclass(frozen=True)
class TargetBatch:
    evaluated_count: int
    affected_count: int
    protected_count: int = 0
    skipped_count: int = 0
    details: dict[str, int | str | bool] = field(default_factory=dict)
    affected_bytes: int | None = None


@dataclass(frozen=True)
class _CandidateQuery:
    model: type
    timestamp: object
    predicate: object


def preview_lifecycle_target(
    db: Session,
    *,
    target_key: str,
    cutoff: datetime,
    options: dict[str, bool] | None = None,
    now: datetime | None = None,
) -> TargetPreview:
    current_time = now or datetime.now(timezone.utc)
    if target_key == "article_content":
        return _preview_article_content(db, cutoff=cutoff, options=options or {})
    if target_key == "integration_delivery_history":
        return _preview_integration_delivery_history(
            db,
            cutoff=cutoff,
            now=current_time,
        )
    if target_key == "ai_task_history":
        return _preview_ai_task_history(db, cutoff=cutoff, now=current_time)
    if target_key == "closed_alert_history":
        return _preview_closed_alert_history(db, cutoff=cutoff, now=current_time)
    query = _candidate_query(target_key, cutoff=cutoff, now=current_time)
    parent_limit = (
        DEPENDENCY_PREVIEW_PARENT_LIMIT
        if has_lifecycle_dependants(query.model)
        else PREVIEW_COUNT_LIMIT
    )
    rows = db.execute(
        select(query.model.id, query.timestamp)
        .where(query.predicate)
        .order_by(query.timestamp.asc(), query.model.id.asc())
        .limit(parent_limit + 1)
    ).all()
    lower_bound = len(rows) > parent_limit
    candidate_rows = rows[:parent_limit]
    bounded_rows, oversized_count = _partition_preview_rows(
        db,
        model=query.model,
        rows=candidate_rows,
    )
    return TargetPreview(
        eligible_count=len(bounded_rows),
        protected_count=oversized_count,
        protected_counts=(
            {"dependent_row_limit": oversized_count} if oversized_count else {}
        ),
        oldest_candidate_at=(bounded_rows[0][1] if bounded_rows else None),
        count_is_lower_bound=lower_bound,
        is_partial=lower_bound,
    )


def execute_lifecycle_target_batch(
    db: Session,
    *,
    target_key: str,
    cutoff: datetime,
    batch_size: int,
    run_id: uuid.UUID,
    options: dict[str, bool] | None = None,
    now: datetime | None = None,
) -> TargetBatch:
    current_time = now or datetime.now(timezone.utc)
    bounded_batch = max(1, min(int(batch_size), 1_000))
    if target_key in {
        "audit_logs",
        "action_approval_history",
        "ai_task_history",
        "ai_usage_history",
    }:
        lock_data_policy_revision_for_derivation(db)
    if target_key == "article_content":
        return _purge_article_content(
            db,
            cutoff=cutoff,
            batch_size=bounded_batch,
            run_id=run_id,
            options=options or {},
        )
    if target_key == "action_approval_history":
        deleted, receipts, operations = _delete_action_approval_history(
            db,
            cutoff=cutoff,
            now=current_time,
            batch_size=bounded_batch,
            max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
        )
        return TargetBatch(
            evaluated_count=deleted,
            affected_count=deleted,
            details={
                "execution_receipts_deleted": receipts,
                "operation_receipts_deleted": operations,
            },
        )
    if target_key == "ai_task_history":
        return _delete_ai_task_history(
            db,
            cutoff=cutoff,
            batch_size=bounded_batch,
        )
    if target_key == "ai_usage_history":
        count = _delete_ai_history_with_envelopes(
            db,
            AIUsageEvent,
            AIUsageEvent.created_at,
            cutoff,
            bounded_batch,
            resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
        )
        return TargetBatch(count, count)
    if target_key == "inactive_auth_sessions":
        query = _candidate_query(target_key, cutoff=cutoff, now=current_time)
        return _delete_generic(
            db,
            target_key=target_key,
            query=query,
            batch_size=bounded_batch,
        )
    if (
        target_key.startswith("integration_")
        and target_key != "integration_run_history"
    ):
        return _prune_integration_target(
            db,
            target_key=target_key,
            cutoff=cutoff,
            now=current_time,
            batch_size=bounded_batch,
        )
    if target_key in {
        "closed_alert_history",
        "alert_evaluation_history",
        "alert_metrics",
    }:
        return _prune_alert_target(
            db,
            target_key=target_key,
            cutoff=cutoff,
            now=current_time,
            batch_size=bounded_batch,
        )
    query = _candidate_query(target_key, cutoff=cutoff, now=current_time)
    return _delete_generic(
        db,
        target_key=target_key,
        query=query,
        batch_size=bounded_batch,
    )


def _candidate_query(
    target_key: str,
    *,
    cutoff: datetime,
    now: datetime,
) -> _CandidateQuery:
    if target_key == "audit_logs":
        return _CandidateQuery(
            AuditLog, AuditLog.created_at, AuditLog.created_at < cutoff
        )
    if target_key == "action_approval_history":
        predicate = and_(
            ActionApprovalRequest.created_at < cutoff,
            or_(
                ActionApprovalRequest.status.in_(
                    ("denied", "cancelled", "invalidated", "executed")
                ),
                ActionApprovalRequest.expires_at <= now,
            ),
        )
        return _CandidateQuery(
            ActionApprovalRequest,
            ActionApprovalRequest.created_at,
            predicate,
        )
    if target_key == "ai_task_history":
        predicate = and_(
            AITaskRun.finished_at.is_not(None),
            AITaskRun.finished_at < cutoff,
            ~select(Report.id)
            .where(Report.request_task_run_id == AITaskRun.id)
            .exists(),
            ~select(AIProviderAttemptReceipt.id)
            .where(
                AIProviderAttemptReceipt.task_run_id_snapshot == AITaskRun.id,
                _unresolved_ai_provider_receipt(AIProviderAttemptReceipt),
            )
            .exists(),
            ~_retained_action_approval_run_reference(AITaskRun.id),
        )
        return _CandidateQuery(AITaskRun, AITaskRun.finished_at, predicate)
    if target_key == "ai_usage_history":
        return _CandidateQuery(
            AIUsageEvent,
            AIUsageEvent.created_at,
            AIUsageEvent.created_at < cutoff,
        )
    if target_key == "tag_feedback_history":
        return _CandidateQuery(
            TagFeedbackEvent,
            TagFeedbackEvent.created_at,
            TagFeedbackEvent.created_at < cutoff,
        )
    if target_key == "integration_run_history":
        return _CandidateQuery(
            IntegrationRun,
            IntegrationRun.finished_at,
            and_(
                IntegrationRun.finished_at.is_not(None),
                IntegrationRun.finished_at < cutoff,
            ),
        )
    if target_key == "inactive_auth_sessions":
        session_end = func.coalesce(
            AuthSession.revoked_at,
            func.least(AuthSession.idle_expires_at, AuthSession.absolute_expires_at),
        )
        return _CandidateQuery(
            AuthSession,
            session_end,
            and_(
                or_(
                    AuthSession.revoked_at.is_not(None),
                    AuthSession.idle_expires_at < now,
                    AuthSession.absolute_expires_at < now,
                ),
                session_end < cutoff,
            ),
        )
    if target_key == "system_health_samples":
        return _CandidateQuery(
            SystemHealthSample,
            SystemHealthSample.sampled_at,
            SystemHealthSample.sampled_at < cutoff,
        )
    if target_key == "integration_delivery_history":
        terminal_at = func.coalesce(
            IntegrationDelivery.completed_at,
            IntegrationDelivery.dead_lettered_at,
            IntegrationDelivery.updated_at,
        )
        return _CandidateQuery(
            IntegrationDelivery,
            terminal_at,
            and_(*_integration_delivery_retention_predicates(cutoff=cutoff)),
        )
    if target_key == "integration_event_history":
        return _CandidateQuery(
            IntegrationEvent,
            IntegrationEvent.created_at,
            and_(
                IntegrationEvent.routing_state.in_(("routed", "dead_letter")),
                IntegrationEvent.created_at < cutoff,
                ~exists(
                    select(IntegrationDelivery.id).where(
                        IntegrationDelivery.event_id == IntegrationEvent.id
                    )
                ),
            ),
        )
    if target_key == "integration_metrics":
        return _CandidateQuery(
            IntegrationDeliveryMetric,
            IntegrationDeliveryMetric.bucket_start,
            IntegrationDeliveryMetric.bucket_start < cutoff,
        )
    if target_key == "closed_alert_history":
        return _CandidateQuery(
            AlertOccurrence,
            AlertOccurrence.closed_at,
            and_(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.metrics_aggregated_at.is_not(None),
                AlertOccurrence.closed_at < cutoff,
            ),
        )
    if target_key == "alert_activity_history":
        return _CandidateQuery(
            AlertOccurrenceActivity,
            AlertOccurrenceActivity.created_at,
            and_(
                AlertOccurrenceActivity.created_at < cutoff,
                AlertOccurrenceActivity.action != "created",
            ),
        )
    if target_key == "alert_evaluation_history":
        return _CandidateQuery(
            AlertEvaluationRequest,
            AlertEvaluationRequest.completed_at,
            and_(
                AlertEvaluationRequest.state.in_(("succeeded", "dead_letter")),
                AlertEvaluationRequest.completed_at.is_not(None),
                AlertEvaluationRequest.completed_at < cutoff,
            ),
        )
    if target_key == "alert_metrics":
        return _CandidateQuery(
            AlertOccurrenceMetric,
            AlertOccurrenceMetric.bucket_start,
            AlertOccurrenceMetric.bucket_start < cutoff,
        )
    raise ValueError("Unknown lifecycle target.")


def _preview_integration_delivery_history(
    db: Session,
    *,
    cutoff: datetime,
    now: datetime,
) -> TargetPreview:
    generic = _candidate_query(
        "integration_delivery_history",
        cutoff=cutoff,
        now=now,
    )
    generic_rows = db.execute(
        select(generic.model.id, generic.timestamp)
        .where(generic.predicate)
        .order_by(generic.timestamp.asc(), generic.model.id.asc())
        .limit(DEPENDENCY_PREVIEW_PARENT_LIMIT + 1)
    ).all()
    legacy_rows = db.execute(
        select(
            NotificationWebhookDelivery.id,
            NotificationWebhookDelivery.attempted_at,
        )
        .where(
            *_legacy_notification_delivery_retention_predicates(cutoff=cutoff)
        )
        .order_by(
            NotificationWebhookDelivery.attempted_at.asc(),
            NotificationWebhookDelivery.id.asc(),
        )
        .limit(DEPENDENCY_PREVIEW_PARENT_LIMIT + 1)
    ).all()
    generic_lower_bound = len(generic_rows) > DEPENDENCY_PREVIEW_PARENT_LIMIT
    legacy_lower_bound = len(legacy_rows) > DEPENDENCY_PREVIEW_PARENT_LIMIT
    generic_rows = generic_rows[:DEPENDENCY_PREVIEW_PARENT_LIMIT]
    legacy_rows = legacy_rows[:DEPENDENCY_PREVIEW_PARENT_LIMIT]
    generic_rows, generic_oversized = _partition_preview_rows(
        db,
        model=IntegrationDelivery,
        rows=generic_rows,
    )
    legacy_rows, legacy_oversized = _partition_preview_rows(
        db,
        model=NotificationWebhookDelivery,
        rows=legacy_rows,
    )
    bounded_total = len(generic_rows) + len(legacy_rows)
    count_is_lower_bound = (
        generic_lower_bound
        or legacy_lower_bound
        or bounded_total > PREVIEW_COUNT_LIMIT
    )
    oldest_candidates = [
        rows[0][1] for rows in (generic_rows, legacy_rows) if rows
    ]
    return TargetPreview(
        eligible_count=min(bounded_total, PREVIEW_COUNT_LIMIT),
        protected_count=generic_oversized + legacy_oversized,
        protected_counts=(
            {"dependent_row_limit": generic_oversized + legacy_oversized}
            if generic_oversized or legacy_oversized
            else {}
        ),
        oldest_candidate_at=min(oldest_candidates) if oldest_candidates else None,
        count_is_lower_bound=count_is_lower_bound,
        is_partial=count_is_lower_bound,
    )


def _preview_ai_task_history(
    db: Session,
    *,
    cutoff: datetime,
    now: datetime,
) -> TargetPreview:
    tasks = _candidate_query("ai_task_history", cutoff=cutoff, now=now)
    task_rows = db.execute(
        select(tasks.model.id, tasks.timestamp)
        .where(tasks.predicate)
        .order_by(tasks.timestamp.asc(), tasks.model.id.asc())
        .limit(DEPENDENCY_PREVIEW_PARENT_LIMIT + 1)
    ).all()
    task_lower_bound = len(task_rows) > DEPENDENCY_PREVIEW_PARENT_LIMIT
    task_rows = task_rows[:DEPENDENCY_PREVIEW_PARENT_LIMIT]
    task_rows, task_oversized = _partition_preview_rows(
        db,
        model=AITaskRun,
        rows=task_rows,
    )
    operation_ids = _eligible_ai_provider_receipt_operation_ids(
        db,
        cutoff=cutoff,
        batch_size=PREVIEW_COUNT_LIMIT + 1,
    )
    receipt_rows = (
        db.execute(
            select(
                AIProviderAttemptReceipt.id,
                AIProviderAttemptReceipt.updated_at,
            )
            .where(AIProviderAttemptReceipt.operation_id.in_(operation_ids))
            .order_by(
                AIProviderAttemptReceipt.updated_at.asc(),
                AIProviderAttemptReceipt.id.asc(),
            )
            .limit(PREVIEW_COUNT_LIMIT + 1)
        ).all()
        if operation_ids
        else []
    )
    receipt_lower_bound = len(receipt_rows) > PREVIEW_COUNT_LIMIT
    bounded_total = len(task_rows) + len(receipt_rows)
    count_is_lower_bound = (
        task_lower_bound
        or receipt_lower_bound
        or bounded_total > PREVIEW_COUNT_LIMIT
    )
    oldest_candidates = [
        rows[0][1] for rows in (task_rows, receipt_rows) if rows
    ]
    return TargetPreview(
        eligible_count=min(bounded_total, PREVIEW_COUNT_LIMIT),
        protected_count=task_oversized,
        protected_counts=(
            {"dependent_row_limit": task_oversized} if task_oversized else {}
        ),
        oldest_candidate_at=min(oldest_candidates) if oldest_candidates else None,
        count_is_lower_bound=count_is_lower_bound,
        is_partial=count_is_lower_bound,
    )


def _preview_closed_alert_history(
    db: Session,
    *,
    cutoff: datetime,
    now: datetime,
) -> TargetPreview:
    candidates = _candidate_query("closed_alert_history", cutoff=cutoff, now=now)
    occurrence_rows = db.execute(
        select(candidates.model.id, candidates.timestamp)
        .where(candidates.predicate)
        .order_by(candidates.timestamp.asc(), candidates.model.id.asc())
        .limit(PREVIEW_COUNT_LIMIT + 1)
    ).all()
    occurrence_ids = [row[0] for row in occurrence_rows]
    activity_rows = (
        db.execute(
            select(AlertOccurrenceActivity.id)
            .where(AlertOccurrenceActivity.occurrence_id.in_(occurrence_ids))
            .order_by(
                AlertOccurrenceActivity.occurrence_id,
                AlertOccurrenceActivity.created_at,
                AlertOccurrenceActivity.id,
            )
            .limit(PREVIEW_COUNT_LIMIT + 1)
        ).all()
        if occurrence_ids
        else []
    )
    awaiting_rollup_ids = list(
        db.scalars(
            select(AlertOccurrence.id)
            .where(
                AlertOccurrence.lifecycle_state == "closed",
                AlertOccurrence.closed_at.is_not(None),
                AlertOccurrence.closed_at < cutoff,
                AlertOccurrence.metrics_aggregated_at.is_(None),
            )
            .order_by(AlertOccurrence.closed_at, AlertOccurrence.id)
            .limit(PREVIEW_COUNT_LIMIT + 1)
        ).all()
    )
    bounded_total = len(occurrence_rows) + len(activity_rows)
    count_is_lower_bound = (
        bounded_total > PREVIEW_COUNT_LIMIT
        or len(awaiting_rollup_ids) > PREVIEW_COUNT_LIMIT
    )
    protected_count = min(len(awaiting_rollup_ids), PREVIEW_COUNT_LIMIT)
    return TargetPreview(
        eligible_count=min(bounded_total, PREVIEW_COUNT_LIMIT),
        protected_count=protected_count,
        protected_counts={"awaiting_metric_rollup": protected_count},
        oldest_candidate_at=(occurrence_rows[0][1] if occurrence_rows else None),
        count_is_lower_bound=count_is_lower_bound,
        is_partial=count_is_lower_bound,
    )


def _partition_preview_rows(
    db: Session,
    *,
    model,
    rows: list,
) -> tuple[list, int]:
    candidate_rows = rows[:PREVIEW_COUNT_LIMIT]
    eligible_ids, oversized_ids = partition_oversized_lifecycle_parents(
        db,
        model=model,
        parent_ids=[row[0] for row in candidate_rows],
    )
    return (
        [row for row in candidate_rows if row[0] in eligible_ids],
        len(oversized_ids),
    )


def _delete_generic(
    db: Session,
    *,
    target_key: str,
    query: _CandidateQuery,
    batch_size: int,
) -> TargetBatch:
    ids = list(
        db.scalars(
            select(query.model.id)
            .where(query.predicate)
            .order_by(query.timestamp.asc(), query.model.id.asc())
            .limit(lifecycle_parent_scan_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not ids:
        return TargetBatch(0, 0)
    selection = select_with_dependent_budget(
        db,
        model=query.model,
        candidate_ids=ids,
        max_parent_records=batch_size,
    )
    ids = selection.ids
    if not ids:
        return TargetBatch(
            evaluated_count=selection.oversized_count,
            affected_count=0,
            protected_count=selection.oversized_count,
            skipped_count=selection.oversized_count,
            details={
                "dependent_row_limit": MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
                "oversized_parents_skipped": selection.oversized_count,
            },
        )
    result = db.execute(
        delete(query.model)
        .where(query.model.id.in_(ids), query.predicate)
        .execution_options(synchronize_session=False)
    )
    affected = int(result.rowcount or 0)
    return TargetBatch(
        evaluated_count=len(ids),
        affected_count=affected,
        protected_count=selection.oversized_count,
        skipped_count=max(0, len(ids) - affected) + selection.oversized_count,
        details={
            "dependent_rows_budgeted": selection.dependent_rows,
            "dependent_row_limit": MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
            "oversized_parents_skipped": selection.oversized_count,
            "dependent_budget_exhausted": selection.budget_exhausted,
            "target": target_key,
        },
    )


def _article_base_predicate(cutoff: datetime):
    age = func.coalesce(Item.published_at, Item.first_seen_at)
    payload_present = or_(
        Article.text.is_not(None),
        Article.title_extracted.is_not(None),
        Article.language.is_not(None),
        Article.word_count.is_not(None),
    )
    return age, and_(
        age < cutoff,
        Article.content_purged_at.is_(None),
        payload_present,
    )


def _article_protections(options: dict[str, bool]) -> dict[str, object]:
    protections: dict[str, object] = {
        "active_ai_work": exists(
            select(AITaskRun.id).where(
                AITaskRun.item_id == Item.id,
                AITaskRun.status.in_(("queued", "running")),
            )
        ),
        "active_alert_evaluation": exists(
            select(AlertEvaluationRequest.id).where(
                AlertEvaluationRequest.item_id == Item.id,
                AlertEvaluationRequest.state.in_(
                    ("pending", "processing", "retry_wait")
                ),
            )
        ),
    }
    if options.get("protect_starred", True):
        protections["starred"] = exists(
            select(ItemState.item_id).where(
                ItemState.item_id == Item.id,
                ItemState.is_starred.is_(True),
            )
        )
    if options.get("protect_notes", True):
        protections["notes"] = exists(
            select(ItemState.item_id).where(
                ItemState.item_id == Item.id,
                func.length(func.btrim(func.coalesce(ItemState.note, ""))) > 0,
            )
        )
    if options.get("protect_investigations", True):
        protections["investigation_evidence"] = exists(
            select(InvestigationEvidence.id).where(
                InvestigationEvidence.source_type == "item",
                InvestigationEvidence.source_id == Item.id,
            )
        )
    if options.get("protect_reports", True):
        protections["report_source"] = exists(
            select(ReportSourceItem.id).where(ReportSourceItem.item_id == Item.id)
        )
    if options.get("protect_active_alerts", True):
        protections["active_alert"] = exists(
            select(AlertOccurrence.id).where(
                AlertOccurrence.lifecycle_state != "closed",
                or_(
                    AlertOccurrence.item_id == Item.id,
                    AlertOccurrence.item_id_snapshot == Item.id,
                ),
            )
        )
    return protections


def _article_candidate_statement(
    *, cutoff: datetime, options: dict[str, bool], include_protected: bool = False
):
    age, base = _article_base_predicate(cutoff)
    protections = _article_protections(options)
    predicate = base
    if protections and not include_protected:
        predicate = and_(predicate, ~or_(*protections.values()))
    return (
        select(Article.id, age).join(Item, Item.id == Article.item_id).where(predicate),
        protections,
        base,
    )


def _preview_article_content(
    db: Session,
    *,
    cutoff: datetime,
    options: dict[str, bool],
) -> TargetPreview:
    statement, protections, base = _article_candidate_statement(
        cutoff=cutoff,
        options=options,
    )
    rows = db.execute(
        statement.order_by(
            func.coalesce(Item.published_at, Item.first_seen_at), Article.id
        ).limit(PREVIEW_COUNT_LIMIT + 1)
    ).all()
    lower_bound = len(rows) > PREVIEW_COUNT_LIMIT
    rows = rows[:PREVIEW_COUNT_LIMIT]
    protected_counts: dict[str, int] = {}
    protected_lower_bound = False
    for key, predicate in protections.items():
        protected_ids = list(
            db.scalars(
                select(Article.id)
                .join(Item, Item.id == Article.item_id)
                .where(base, predicate)
                .limit(PREVIEW_COUNT_LIMIT + 1)
            ).all()
        )
        protected_counts[key] = min(PREVIEW_COUNT_LIMIT, len(protected_ids))
        protected_lower_bound = (
            protected_lower_bound or len(protected_ids) > PREVIEW_COUNT_LIMIT
        )
    protected_count = 0
    if protections:
        protected_ids = list(
            db.scalars(
                select(Article.id)
                .join(Item, Item.id == Article.item_id)
                .where(base, or_(*protections.values()))
                .limit(PREVIEW_COUNT_LIMIT + 1)
            ).all()
        )
        protected_count = min(PREVIEW_COUNT_LIMIT, len(protected_ids))
        protected_lower_bound = (
            protected_lower_bound or len(protected_ids) > PREVIEW_COUNT_LIMIT
        )
    eligible_ids = [row[0] for row in rows]
    eligible_bytes = _article_payload_bytes(db, eligible_ids)
    return TargetPreview(
        eligible_count=len(rows),
        protected_count=protected_count,
        protected_counts=protected_counts,
        oldest_candidate_at=(rows[0][1] if rows else None),
        count_is_lower_bound=lower_bound or protected_lower_bound,
        is_partial=lower_bound or protected_lower_bound,
        eligible_bytes=eligible_bytes,
    )


def _purge_article_content(
    db: Session,
    *,
    cutoff: datetime,
    batch_size: int,
    run_id: uuid.UUID,
    options: dict[str, bool],
) -> TargetBatch:
    _lock_article_safeguard_tables(db, options=options)
    statement, _protections, _base = _article_candidate_statement(
        cutoff=cutoff,
        options=options,
    )
    bounded_batch = min(batch_size, ARTICLE_BATCH_RECORD_LIMIT)
    item_ids = list(
        db.scalars(
            statement.with_only_columns(Item.id)
            .order_by(func.coalesce(Item.published_at, Item.first_seen_at), Article.id)
            .limit(bounded_batch)
            .with_for_update(of=Item, skip_locked=True)
        ).all()
    )
    if not item_ids:
        return TargetBatch(0, 0)
    candidate_subquery, _protections, _base = _article_candidate_statement(
        cutoff=cutoff,
        options=options,
    )
    eligible_rows = list(
        db.execute(
            candidate_subquery.with_only_columns(
                Article.id,
                _article_payload_size_expression().label("payload_bytes"),
            )
            .where(Item.id.in_(item_ids))
            .order_by(func.coalesce(Item.published_at, Item.first_seen_at), Article.id)
            .with_for_update(of=Article)
        ).all()
    )
    eligible_ids: list[uuid.UUID] = []
    selected_bytes = 0
    for article_id, payload_bytes in eligible_rows:
        size = int(payload_bytes or 0)
        if eligible_ids and selected_bytes + size > ARTICLE_BATCH_BYTE_LIMIT:
            break
        eligible_ids.append(article_id)
        selected_bytes += size
    affected_bytes = _article_payload_bytes(db, eligible_ids)
    result = db.execute(
        update(Article)
        .where(Article.id.in_(eligible_ids))
        .values(
            title_extracted=None,
            text=None,
            extraction_method="retention_purged",
            language=None,
            word_count=None,
            content_purged_at=datetime.now(timezone.utc),
            content_purge_run_id=run_id,
        )
        .execution_options(synchronize_session=False)
    )
    affected = int(result.rowcount or 0)
    return TargetBatch(
        evaluated_count=len(eligible_ids),
        affected_count=affected,
        skipped_count=max(0, len(eligible_ids) - affected),
        details={"records_preserved": affected},
        affected_bytes=affected_bytes if affected else 0,
    )


def _lock_article_safeguard_tables(
    db: Session,
    *,
    options: dict[str, bool],
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    tables = ["ai_task_runs", "alert_evaluation_requests"]
    configurable_tables = {
        "protect_starred": "item_state",
        "protect_notes": "item_state",
        "protect_investigations": "investigation_evidence",
        "protect_reports": "report_source_items",
        "protect_active_alerts": "alert_occurrences",
    }
    tables.extend(
        table
        for option, table in configurable_tables.items()
        if options.get(option, True)
    )
    db.execute(text(f"LOCK TABLE {', '.join(sorted(set(tables)))} IN SHARE MODE"))


def _article_payload_bytes(db: Session, article_ids: list[uuid.UUID]) -> int:
    if not article_ids:
        return 0
    value = db.scalar(
        select(func.sum(_article_payload_size_expression())).where(
            Article.id.in_(article_ids)
        )
    )
    return int(value or 0)


def _article_payload_size_expression():
    return (
        func.octet_length(func.coalesce(Article.text, ""))
        + func.octet_length(func.coalesce(Article.title_extracted, ""))
        + func.octet_length(func.coalesce(Article.language, ""))
    )


def _delete_ai_task_history(
    db: Session,
    *,
    cutoff: datetime,
    batch_size: int,
) -> TargetBatch:
    predicate = and_(
        AITaskRun.finished_at.is_not(None),
        ~select(Report.id).where(Report.request_task_run_id == AITaskRun.id).exists(),
        ~select(AIProviderAttemptReceipt.id)
        .where(
            AIProviderAttemptReceipt.task_run_id_snapshot == AITaskRun.id,
            _unresolved_ai_provider_receipt(AIProviderAttemptReceipt),
        )
        .exists(),
        ~_retained_action_approval_run_reference(AITaskRun.id),
    )
    task_count = _delete_ai_history_with_envelopes(
        db,
        AITaskRun,
        AITaskRun.finished_at,
        cutoff,
        batch_size,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        extra_predicate=predicate,
        max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    )
    remaining = max(0, batch_size - task_count)
    receipt_count = (
        _delete_expired_ai_provider_receipt_ledgers(
            db,
            cutoff=cutoff,
            batch_size=remaining,
        )
        if remaining
        else 0
    )
    return TargetBatch(
        evaluated_count=task_count + receipt_count,
        affected_count=task_count + receipt_count,
        details={
            "task_runs_deleted": task_count,
            "provider_receipts_deleted": receipt_count,
        },
    )


def _prune_integration_target(
    db: Session,
    *,
    target_key: str,
    cutoff: datetime,
    now: datetime,
    batch_size: int,
) -> TargetBatch:
    result = prune_integration_delivery_history(
        db,
        now=now,
        batch_size=batch_size,
        delivery_cutoff_at=cutoff,
        event_cutoff_at=cutoff,
        metric_cutoff_at=cutoff,
        prune_deliveries=target_key == "integration_delivery_history",
        prune_legacy_deliveries=target_key == "integration_delivery_history",
        prune_events=target_key == "integration_event_history",
        prune_metrics=target_key == "integration_metrics",
        prune_orphans=False,
        max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
        commit=False,
    )
    primary_key = {
        "integration_delivery_history": "deliveries_deleted",
        "integration_event_history": "events_deleted",
        "integration_metrics": "metrics_deleted",
    }[target_key]
    affected = int(result[primary_key])
    if target_key == "integration_delivery_history":
        affected += int(result["legacy_webhook_deliveries_deleted"])
    details = {
        key: int(value)
        for key, value in result.items()
        if isinstance(value, int) and value
    }
    return TargetBatch(affected, affected, details=details)


def _prune_alert_target(
    db: Session,
    *,
    target_key: str,
    cutoff: datetime,
    now: datetime,
    batch_size: int,
) -> TargetBatch:
    if target_key == "closed_alert_history":
        return _prune_closed_alert_history(
            db,
            cutoff=cutoff,
            now=now,
            batch_size=batch_size,
        )
    result = maintain_alert_history(
        db,
        now=now,
        batch_size=batch_size,
        occurrence_retention_days=_ALERT_RETENTION_DISABLED_DAYS,
        activity_retention_days=_ALERT_RETENTION_DISABLED_DAYS,
        evaluation_retention_days=_ALERT_RETENTION_DISABLED_DAYS,
        metric_retention_days=_ALERT_RETENTION_DISABLED_DAYS,
        occurrence_cutoff_at=None,
        evaluation_cutoff_at=(
            cutoff if target_key == "alert_evaluation_history" else None
        ),
        metric_cutoff_at=(cutoff if target_key == "alert_metrics" else None),
        max_batches=1,
        commit=False,
        prune_expired_previews=False,
        aggregate_occurrences=False,
        prune_occurrences=False,
        prune_activities=False,
        prune_evaluations=target_key == "alert_evaluation_history",
        prune_metrics=target_key == "alert_metrics",
        max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    )
    primary = {
        "alert_evaluation_history": result.evaluations_deleted,
        "alert_metrics": result.metrics_deleted,
    }[target_key]
    details = {
        "metric_rollups_created": result.occurrences_aggregated,
        "dependent_row_limit": MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    }
    return TargetBatch(primary, primary, details=details)


def _prune_closed_alert_history(
    db: Session,
    *,
    cutoff: datetime,
    now: datetime,
    batch_size: int,
) -> TargetBatch:
    candidates = _candidate_query("closed_alert_history", cutoff=cutoff, now=now)
    occurrence_ids = list(
        db.scalars(
            select(AlertOccurrence.id)
            .where(candidates.predicate)
            .order_by(candidates.timestamp.asc(), AlertOccurrence.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not occurrence_ids:
        return TargetBatch(0, 0)
    activity_ids = list(
        db.scalars(
            select(AlertOccurrenceActivity.id)
            .where(AlertOccurrenceActivity.occurrence_id.in_(occurrence_ids))
            .order_by(
                AlertOccurrenceActivity.occurrence_id,
                AlertOccurrenceActivity.created_at,
                AlertOccurrenceActivity.id,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
    )
    activities_deleted = 0
    if activity_ids:
        activity_result = db.execute(
            delete(AlertOccurrenceActivity)
            .where(AlertOccurrenceActivity.id.in_(activity_ids))
            .execution_options(synchronize_session=False)
        )
        activities_deleted = int(activity_result.rowcount or 0)
        db.flush()
    remaining_budget = max(0, batch_size - activities_deleted)
    deleted_occurrence_ids: list[uuid.UUID] = []
    if remaining_budget:
        ready_ids = list(
            db.scalars(
                select(AlertOccurrence.id)
                .where(
                    AlertOccurrence.id.in_(occurrence_ids),
                    candidates.predicate,
                    ~exists(
                        select(AlertOccurrenceActivity.id).where(
                            AlertOccurrenceActivity.occurrence_id
                            == AlertOccurrence.id
                        )
                    ),
                )
                .order_by(candidates.timestamp.asc(), AlertOccurrence.id.asc())
                .limit(remaining_budget)
            ).all()
        )
        if ready_ids:
            deleted_occurrence_ids = list(
                db.scalars(
                    delete(AlertOccurrence)
                    .where(
                        AlertOccurrence.id.in_(ready_ids),
                        candidates.predicate,
                    )
                    .returning(AlertOccurrence.id)
                    .execution_options(synchronize_session=False)
                ).all()
            )
    envelopes_deleted = 0
    if deleted_occurrence_ids:
        db.flush()
        envelopes_deleted = prune_deleted_resource_envelopes(
            db,
            resources=(
                (DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE, occurrence_id)
                for occurrence_id in deleted_occurrence_ids
            ),
            max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
        )
    affected = activities_deleted + len(deleted_occurrence_ids)
    return TargetBatch(
        evaluated_count=affected,
        affected_count=affected,
        details={
            "alert_occurrence_activities_deleted": activities_deleted,
            "alert_occurrences_deleted": len(deleted_occurrence_ids),
            "data_access_envelopes_deleted": envelopes_deleted,
        },
    )


__all__ = [
    "PREVIEW_COUNT_LIMIT",
    "TargetBatch",
    "TargetPreview",
    "execute_lifecycle_target_batch",
    "preview_lifecycle_target",
]
