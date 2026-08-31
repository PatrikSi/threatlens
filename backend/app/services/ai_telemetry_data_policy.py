from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import and_, exists, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.models.ai_task_run import AITaskRun
from app.models.ai_task_event import AITaskEvent
from app.models.ai_usage_event import AIUsageEvent
from app.models.audit_log import (
    AuditLog,
    AuditLogDataAccessFeed,
    AuditLogDataAccessLabel,
)
from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_telemetry_lineage import (
    _copy_child_run_lineage,
    _copy_resource_lineage_if_present,
    _ensure_quarantined_if_empty,
    _locked_feed_sources,
    _locked_item_sources,
    _merge_quarantine_source,
    _unique_uuids,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_REPORT,
    data_access_envelope_predicate,
    merge_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataAccessContext,
    fence_data_access_context,
)
from app.services.data_access_runtime import (
    lock_data_policy_revision_for_derivation,
)
from app.schemas.ai import (
    AITaskEventResponse,
    AITaskRunDetailResponse,
    AITaskRunListResponse,
)


AI_DATA_ACCESS_SCOPE_SYSTEM = "system"
AI_DATA_ACCESS_SCOPE_GOVERNED = "governed"

_DIRECT_TASK_TYPE_ITEM = "item_enrichment"
_DIRECT_TASK_TYPE_DAILY_BRIEF = "daily_brief"
_DIRECT_TASK_TYPE_REPORT = "report"
_SYSTEM_TASK_TYPE = "connection_test"


@dataclass(frozen=True, slots=True)
class AITelemetryWouldDenySummary:
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def ai_task_run_access_predicate(data_access: DataAccessContext):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    system_envelope = _resource_envelope_exists(
        DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        AITaskRun.id,
    )
    return or_(
        and_(
            AITaskRun.data_access_scope == AI_DATA_ACCESS_SCOPE_SYSTEM,
            AITaskRun.task_type == _SYSTEM_TASK_TYPE,
            AITaskRun.data_access_lineage_complete.is_(True),
            AITaskRun.item_id.is_(None),
            AITaskRun.daily_brief_id.is_(None),
            AITaskRun.report_id.is_(None),
            AITaskRun.parent_run_id.is_(None),
            ~system_envelope,
        ),
        and_(
            AITaskRun.data_access_scope == AI_DATA_ACCESS_SCOPE_GOVERNED,
            AITaskRun.data_access_lineage_complete.is_(True),
            data_access_envelope_predicate(
                DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                AITaskRun.id,
                data_access,
            ),
        ),
    )


def ai_usage_event_access_predicate(data_access: DataAccessContext):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    usage_envelope = _resource_envelope_exists(
        DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        AIUsageEvent.id,
    )
    linked_system_run = or_(
        AIUsageEvent.task_run_id_snapshot.is_(None),
        exists(
            select(AITaskRun.id).where(
                AITaskRun.id == AIUsageEvent.task_run_id_snapshot,
                AITaskRun.data_access_scope == AI_DATA_ACCESS_SCOPE_SYSTEM,
                AITaskRun.task_type == _SYSTEM_TASK_TYPE,
                AITaskRun.data_access_lineage_complete.is_(True),
                AITaskRun.item_id.is_(None),
                AITaskRun.daily_brief_id.is_(None),
                AITaskRun.report_id.is_(None),
                AITaskRun.parent_run_id.is_(None),
                ~_resource_envelope_exists(
                    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                    AITaskRun.id,
                ),
            )
        ),
    )
    return or_(
        and_(
            AIUsageEvent.data_access_scope == AI_DATA_ACCESS_SCOPE_SYSTEM,
            AIUsageEvent.feature_type == _SYSTEM_TASK_TYPE,
            AIUsageEvent.item_id.is_(None),
            AIUsageEvent.daily_brief_id.is_(None),
            AIUsageEvent.report_id.is_(None),
            ~usage_envelope,
            linked_system_run,
        ),
        and_(
            AIUsageEvent.data_access_scope == AI_DATA_ACCESS_SCOPE_GOVERNED,
            data_access_envelope_predicate(
                DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
                AIUsageEvent.id,
                data_access,
            ),
        ),
    )


def ai_audit_history_access_predicate(data_access: DataAccessContext):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    label = aliased(AuditLogDataAccessLabel)
    active_label = aliased(HandlingLabel)
    any_label = exists(
        select(label.audit_log_id).where(label.audit_log_id == AuditLog.id)
    )
    inaccessible_label = exists(
        select(label.audit_log_id)
        .join(active_label, active_label.id == label.label_id)
        .where(
            label.audit_log_id == AuditLog.id,
            or_(
                label.label_id.not_in(data_access.allowed_label_ids),
                active_label.is_active.is_(False),
            ),
        )
    )
    return or_(
        AuditLog.data_access_governed.is_(False),
        and_(any_label, ~inaccessible_label),
    )


def list_ai_task_runs_for_data_access(
    db: Session,
    *,
    data_access: DataAccessContext,
    limit: int = 50,
    offset: int = 0,
    task_type: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    model: str | None = None,
    since: datetime | None = None,
    parent_run_id: uuid.UUID | None = None,
    only_failures: bool = False,
) -> AITaskRunListResponse:
    if data_access.principal_eligible and not data_access.enforced:
        from app.services.ai_ops import list_ai_task_runs

        return list_ai_task_runs(
            db,
            limit=limit,
            offset=offset,
            task_type=task_type,
            status=status,
            trigger_source=trigger_source,
            model=model,
            since=since,
            parent_run_id=parent_run_id,
            only_failures=only_failures,
            reconcile_stale=status in {"queued", "running"},
        )

    from app.services.ai_task_projection import _map_run_responses

    filters: list[object] = [ai_task_run_access_predicate(data_access)]
    if task_type:
        filters.append(AITaskRun.task_type == task_type)
    if status:
        filters.append(AITaskRun.status == status)
    if trigger_source:
        filters.append(AITaskRun.trigger_source == trigger_source)
    if model:
        filters.append(AITaskRun.model == model)
    if since:
        filters.append(AITaskRun.created_at >= since)
    if parent_run_id:
        filters.append(AITaskRun.parent_run_id == parent_run_id)
    if only_failures:
        filters.append(or_(AITaskRun.status == "error", AITaskRun.error.is_not(None)))
    total = int(db.scalar(select(func.count(AITaskRun.id)).where(*filters)) or 0)
    runs = list(
        db.scalars(
            select(AITaskRun)
            .where(*filters)
            .order_by(AITaskRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return AITaskRunListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=_map_run_responses(db, runs),
    )


def get_ai_task_run_detail_for_data_access(
    db: Session,
    *,
    run_id: uuid.UUID,
    data_access: DataAccessContext,
) -> AITaskRunDetailResponse | None:
    if data_access.principal_eligible and not data_access.enforced:
        from app.services.ai_ops import get_ai_task_run_detail

        return get_ai_task_run_detail(db, run_id=run_id)

    from app.services.ai_task_projection import _map_run_responses

    run = db.scalar(
        select(AITaskRun).where(
            AITaskRun.id == run_id,
            ai_task_run_access_predicate(data_access),
        )
    )
    if run is None:
        return None
    events = list(
        db.scalars(
            select(AITaskEvent)
            .where(AITaskEvent.task_run_id == run.id)
            .order_by(AITaskEvent.created_at.asc())
        )
    )
    return AITaskRunDetailResponse(
        run=_map_run_responses(db, [run])[0],
        events=[
            AITaskEventResponse(
                id=event.id,
                task_run_id=event.task_run_id,
                event_type=event.event_type,
                message=event.message,
                payload=dict(event.payload_json or {}),
                created_at=event.created_at,
            )
            for event in events
        ],
    )


def cancel_ai_task_run_for_data_access(
    db: Session,
    *,
    run_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    data_access: DataAccessContext,
) -> AITaskRun | None:
    """Authorize before any Celery inspection or cancellation side effect."""

    from app.services.ai_ops import (
        _load_live_task_snapshot,
        _mark_ai_task_run_cancel_requested,
        _normalize_live_task_snapshot,
        finish_ai_task_run,
        record_ai_task_event,
    )
    from app.services.ai_ops_common import (
        AI_STATUS_QUEUED,
        AI_STATUS_RUNNING,
        AI_STATUS_SKIPPED,
        AI_TASK_TYPE_REPORT,
        AI_TASK_TYPE_REPROCESS,
    )
    from app.services.report_task_lineage import resolve_report_task_run
    from app.tasks.celery_app import celery_app

    fence_data_access_context(db, data_access)
    run = db.scalar(
        select(AITaskRun)
        .where(
            AITaskRun.id == run_id,
            ai_task_run_access_predicate(data_access),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.task_type == AI_TASK_TYPE_REPORT:
        run = resolve_report_task_run(db, run, lock=True)
        if (
            db.scalar(
                select(AITaskRun.id).where(
                    AITaskRun.id == run.id,
                    ai_task_run_access_predicate(data_access),
                )
            )
            is None
        ):
            return None

    unfinished = {AI_STATUS_QUEUED, AI_STATUS_RUNNING}
    if run.finished_at is not None or run.status not in unfinished:
        return run
    targets = [run]
    if run.task_type == AI_TASK_TYPE_REPROCESS:
        children = list(
            db.scalars(
                select(AITaskRun)
                .where(
                    AITaskRun.parent_run_id == run.id,
                    AITaskRun.finished_at.is_(None),
                    AITaskRun.status.in_(unfinished),
                )
                .order_by(AITaskRun.id)
                .with_for_update()
            ).all()
        )
        if children:
            accessible_child_ids = set(
                db.scalars(
                    select(AITaskRun.id).where(
                        AITaskRun.id.in_([child.id for child in children]),
                        ai_task_run_access_predicate(data_access),
                    )
                ).all()
            )
            if any(child.id not in accessible_child_ids for child in children):
                return None
        targets = [*children, run]

    snapshot = _normalize_live_task_snapshot(_load_live_task_snapshot())
    snapshot_available, _workers, active, reserved, scheduled = snapshot
    active_ids = {task.celery_task_id for task in active if task.celery_task_id}
    pending_ids = {
        task.celery_task_id for task in [*reserved, *scheduled] if task.celery_task_id
    }
    for target in targets:
        terminate = bool(target.celery_task_id and target.celery_task_id in active_ids)
        removed = bool(
            target.status == AI_STATUS_QUEUED
            and not terminate
            and (
                target.celery_task_id is None
                or target.celery_task_id in pending_ids
                or (snapshot_available and target.celery_task_id not in active_ids)
            )
        )
        revoke_failed = False
        if target.celery_task_id:
            try:
                celery_app.control.revoke(
                    target.celery_task_id,
                    terminate=terminate,
                    signal="SIGTERM",
                )
            except Exception:
                revoke_failed = True
                record_ai_task_event(
                    db,
                    run_id=target.id,
                    event_type="cancel_revoke_failed",
                    payload={"celery_task_id": target.celery_task_id},
                )
        _mark_ai_task_run_cancel_requested(
            db,
            run_id=target.id,
            actor_user_id=actor_user_id,
            removed_from_queue=removed,
            terminated_running_task=terminate,
            revoke_failed=revoke_failed,
        )
        if removed:
            finish_ai_task_run(
                db,
                run_id=target.id,
                status=AI_STATUS_SKIPPED,
                reason="canceled",
                worker_name=target.worker_name,
                model=target.model,
                metadata_updates={
                    "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
                    "cancel_completed_without_worker": True,
                },
            )
    db.flush()
    return db.get(AITaskRun, run.id)


def initialize_ai_task_run_data_access(
    db: Session,
    *,
    run: AITaskRun,
) -> None:
    """Classify a new run and capture immediately available source lineage."""

    if _is_system_run(run):
        run.data_access_scope = AI_DATA_ACCESS_SCOPE_SYSTEM
        run.data_access_lineage_complete = True
        db.add(run)
        db.flush()
        return

    run.data_access_scope = AI_DATA_ACCESS_SCOPE_GOVERNED
    run.data_access_lineage_complete = False
    db.add(run)
    db.flush()
    if run.task_type == _DIRECT_TASK_TYPE_ITEM and run.item_id is not None:
        capture_ai_task_run_data_access(
            db,
            run_id=run.id,
            item_ids=(run.item_id,),
            complete=True,
        )
    elif run.task_type == _DIRECT_TASK_TYPE_DAILY_BRIEF and run.daily_brief_id:
        capture_ai_task_run_data_access(
            db,
            run_id=run.id,
            daily_brief_id=run.daily_brief_id,
            complete=True,
        )
    elif run.task_type == _DIRECT_TASK_TYPE_REPORT and run.report_id:
        capture_ai_task_run_data_access(
            db,
            run_id=run.id,
            report_id=run.report_id,
            complete=True,
        )


def capture_ai_task_run_data_access(
    db: Session,
    *,
    run_id: uuid.UUID,
    item_ids: Iterable[uuid.UUID] = (),
    feed_ids: Iterable[uuid.UUID] = (),
    daily_brief_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    complete: bool,
) -> AITaskRun | None:
    """Monotonically attach durable provenance to a retained AI task run."""

    policy_revision = lock_data_policy_revision_for_derivation(db)
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None
    if run.data_access_scope == AI_DATA_ACCESS_SCOPE_SYSTEM:
        if not _is_system_run(run):
            run.data_access_scope = AI_DATA_ACCESS_SCOPE_GOVERNED
            run.data_access_lineage_complete = False
        else:
            return run

    normalized_item_ids = _unique_uuids(item_ids)
    if normalized_item_ids:
        sources = _locked_item_sources(
            db,
            item_ids=normalized_item_ids,
            policy_revision=policy_revision,
            target_id=run.id,
            captured_at=run.created_at,
        )
        if sources:
            merge_data_access_envelope_sources(
                db,
                resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                resource_id=run.id,
                sources=sources,
            )

    normalized_feed_ids = _unique_uuids(feed_ids)
    if normalized_feed_ids:
        feed_sources = _locked_feed_sources(
            db,
            feed_ids=normalized_feed_ids,
            policy_revision=policy_revision,
            target_id=run.id,
            captured_at=run.created_at,
        )
        if feed_sources:
            merge_data_access_envelope_sources(
                db,
                resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                resource_id=run.id,
                sources=feed_sources,
            )

    _copy_resource_lineage_if_present(
        db,
        source_resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        source_resource_id=daily_brief_id or run.daily_brief_id,
        target_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        target_resource_id=run.id,
    )
    _copy_resource_lineage_if_present(
        db,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=report_id or run.report_id,
        target_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        target_resource_id=run.id,
    )

    if complete:
        _copy_child_run_lineage(db, parent_run_id=run.id)
        if _task_run_shape_is_provable(run):
            _ensure_quarantined_if_empty(
                db,
                resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                resource_id=run.id,
                policy_revision=policy_revision,
                captured_at=run.created_at,
            )
        else:
            _merge_quarantine_source(
                db,
                resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                resource_id=run.id,
                policy_revision=policy_revision,
                captured_at=run.created_at,
            )
        run.data_access_lineage_complete = True
    db.add(run)
    db.flush()
    return run


def complete_ai_task_run_data_access(
    db: Session,
    *,
    run_id: uuid.UUID,
) -> AITaskRun | None:
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    if run is None or run.data_access_lineage_complete:
        return run
    return capture_ai_task_run_data_access(
        db,
        run_id=run_id,
        item_ids=(run.item_id,) if run.item_id else (),
        daily_brief_id=run.daily_brief_id,
        report_id=run.report_id,
        complete=True,
    )


def capture_ai_usage_event_data_access(
    db: Session,
    *,
    event: AIUsageEvent,
    task_run_id: uuid.UUID | None,
) -> None:
    """Persist an immutable policy envelope for one provider usage attempt."""

    policy_revision = lock_data_policy_revision_for_derivation(db)
    event.task_run_id_snapshot = task_run_id
    run = (
        db.scalar(
            select(AITaskRun)
            .where(AITaskRun.id == task_run_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if task_run_id is not None
        else None
    )
    if _is_system_usage(event, run=run, task_run_id=task_run_id):
        event.data_access_scope = AI_DATA_ACCESS_SCOPE_SYSTEM
        db.add(event)
        db.flush()
        return

    event.data_access_scope = AI_DATA_ACCESS_SCOPE_GOVERNED
    db.add(event)
    db.flush()
    if run is not None and run.data_access_lineage_complete:
        _copy_resource_lineage_if_present(
            db,
            source_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            source_resource_id=run.id,
            target_resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            target_resource_id=event.id,
        )
    if event.item_id is not None:
        item_sources = _locked_item_sources(
            db,
            item_ids=(event.item_id,),
            policy_revision=policy_revision,
            target_id=event.id,
            captured_at=event.created_at,
        )
        if item_sources:
            merge_data_access_envelope_sources(
                db,
                resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
                resource_id=event.id,
                sources=item_sources,
            )
    _copy_resource_lineage_if_present(
        db,
        source_resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        source_resource_id=event.daily_brief_id,
        target_resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        target_resource_id=event.id,
    )
    _copy_resource_lineage_if_present(
        db,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=event.report_id,
        target_resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        target_resource_id=event.id,
    )
    if _usage_event_shape_is_provable(event):
        _ensure_quarantined_if_empty(
            db,
            resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            resource_id=event.id,
            policy_revision=policy_revision,
            captured_at=event.created_at,
        )
    else:
        _merge_quarantine_source(
            db,
            resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            resource_id=event.id,
            policy_revision=policy_revision,
            captured_at=event.created_at,
        )
    db.flush()


def taint_ai_audit_history_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
) -> int:
    audit_ids = select(AuditLogDataAccessFeed.audit_log_id).where(
        AuditLogDataAccessFeed.source_feed_id_snapshot == feed_id
    )
    statement = insert(AuditLogDataAccessLabel).from_select(
        ["audit_log_id", "label_id"],
        select(AuditLog.id, literal(handling_label_id)).where(
            AuditLog.id.in_(audit_ids)
        ),
    )
    result = db.execute(statement.on_conflict_do_nothing())
    return int(result.rowcount or 0)


def ai_task_run_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    filters: Sequence[object] = (),
) -> AITelemetryWouldDenySummary:
    return _envelope_would_deny_summary(
        db,
        data_access=data_access,
        model=AITaskRun,
        resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
        active_predicate=ai_task_run_access_predicate,
        filters=filters,
    )


def ai_usage_event_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    filters: Sequence[object] = (),
) -> AITelemetryWouldDenySummary:
    return _envelope_would_deny_summary(
        db,
        data_access=data_access,
        model=AIUsageEvent,
        resource_type=DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
        active_predicate=ai_usage_event_access_predicate,
        filters=filters,
    )


def ai_audit_history_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    filters: Sequence[object] = (),
) -> AITelemetryWouldDenySummary:
    if not data_access.auditing or not data_access.principal_eligible:
        return AITelemetryWouldDenySummary(0, frozenset())
    fence_data_access_context(db, data_access)
    enforced = replace(data_access, mode="enforced")
    denied = ~ai_audit_history_access_predicate(enforced)
    affected_count = int(
        db.scalar(select(func.count(AuditLog.id)).where(*filters, denied)) or 0
    )
    if not affected_count:
        return AITelemetryWouldDenySummary(0, frozenset())
    label_ids = set(
        db.scalars(
            select(AuditLogDataAccessLabel.label_id)
            .join(AuditLog, AuditLog.id == AuditLogDataAccessLabel.audit_log_id)
            .join(
                HandlingLabel,
                HandlingLabel.id == AuditLogDataAccessLabel.label_id,
            )
            .where(
                *filters,
                denied,
                or_(
                    AuditLogDataAccessLabel.label_id.not_in(enforced.allowed_label_ids),
                    HandlingLabel.is_active.is_(False),
                ),
            )
            .distinct()
        ).all()
    )
    unexplained_denial = db.scalar(
        select(AuditLog.id).where(
            *filters,
            denied,
            ~exists(
                select(AuditLogDataAccessLabel.audit_log_id)
                .join(
                    HandlingLabel,
                    HandlingLabel.id == AuditLogDataAccessLabel.label_id,
                )
                .where(
                    AuditLogDataAccessLabel.audit_log_id == AuditLog.id,
                    or_(
                        AuditLogDataAccessLabel.label_id.not_in(
                            enforced.allowed_label_ids
                        ),
                        HandlingLabel.is_active.is_(False),
                    ),
                )
            ),
        )
    )
    if unexplained_denial is not None:
        label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    return AITelemetryWouldDenySummary(affected_count, frozenset(label_ids))


def ai_overview_source_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
) -> AITelemetryWouldDenySummary:
    """Summarize feed-derived overview inputs audit mode currently serves."""

    if not data_access.auditing or not data_access.principal_eligible:
        return AITelemetryWouldDenySummary(0, frozenset())
    fence_data_access_context(db, data_access)
    enforced = replace(data_access, mode="enforced")
    denied_feed = or_(
        Feed.handling_label_id.not_in(enforced.allowed_label_ids),
        HandlingLabel.is_active.is_(False),
    )
    from app.models.article import Article
    from app.models.item_ai_enrichment import ItemAIEnrichment

    contributes_to_overview = or_(
        exists(
            select(Article.id).where(
                Article.item_id == Item.id,
                Article.text.is_not(None),
            )
        ),
        exists(
            select(ItemAIEnrichment.item_id).where(ItemAIEnrichment.item_id == Item.id)
        ),
    )
    denied_item_count = int(
        db.scalar(
            select(func.count(Item.id))
            .join(Feed, Feed.id == Item.feed_id)
            .join(HandlingLabel, HandlingLabel.id == Feed.handling_label_id)
            .where(denied_feed, contributes_to_overview)
        )
        or 0
    )
    from app.models.ai_daily_brief import AIDailyBrief

    denied_brief = ~data_access_envelope_predicate(
        DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        AIDailyBrief.id,
        enforced,
    )
    denied_brief_count = int(
        db.scalar(select(func.count(AIDailyBrief.id)).where(denied_brief)) or 0
    )
    label_ids = set(
        db.scalars(
            select(Feed.handling_label_id)
            .join(Item, Item.feed_id == Feed.id)
            .join(HandlingLabel, HandlingLabel.id == Feed.handling_label_id)
            .where(denied_feed, contributes_to_overview)
            .distinct()
        ).all()
    )
    label_ids.update(
        db.scalars(
            select(DataAccessEnvelopeLabel.label_id)
            .select_from(AIDailyBrief)
            .join(
                DataAccessEnvelope,
                and_(
                    DataAccessEnvelope.resource_type
                    == DATA_ACCESS_RESOURCE_DAILY_BRIEF,
                    DataAccessEnvelope.resource_id == AIDailyBrief.id,
                ),
            )
            .join(
                DataAccessEnvelopeLabel,
                DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
            )
            .join(
                HandlingLabel,
                HandlingLabel.id == DataAccessEnvelopeLabel.label_id,
            )
            .where(
                denied_brief,
                or_(
                    DataAccessEnvelopeLabel.label_id.not_in(enforced.allowed_label_ids),
                    HandlingLabel.is_active.is_(False),
                ),
            )
            .distinct()
        ).all()
    )
    unexplained_brief_denial = db.scalar(
        select(AIDailyBrief.id).where(
            denied_brief,
            ~exists(
                select(DataAccessEnvelope.id)
                .join(
                    DataAccessEnvelopeLabel,
                    DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
                )
                .join(
                    HandlingLabel,
                    HandlingLabel.id == DataAccessEnvelopeLabel.label_id,
                )
                .where(
                    DataAccessEnvelope.resource_type
                    == DATA_ACCESS_RESOURCE_DAILY_BRIEF,
                    DataAccessEnvelope.resource_id == AIDailyBrief.id,
                    or_(
                        DataAccessEnvelopeLabel.label_id.not_in(
                            enforced.allowed_label_ids
                        ),
                        HandlingLabel.is_active.is_(False),
                    ),
                )
            ),
        )
    )
    if unexplained_brief_denial is not None:
        label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    return AITelemetryWouldDenySummary(
        denied_item_count + denied_brief_count,
        frozenset(label_ids),
    )


def _envelope_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    model,
    resource_type: str,
    active_predicate,
    filters: Sequence[object],
) -> AITelemetryWouldDenySummary:
    if not data_access.auditing or not data_access.principal_eligible:
        return AITelemetryWouldDenySummary(0, frozenset())
    fence_data_access_context(db, data_access)
    enforced = replace(data_access, mode="enforced")
    denied = ~active_predicate(enforced)
    affected_count = int(
        db.scalar(select(func.count(model.id)).where(*filters, denied)) or 0
    )
    if not affected_count:
        return AITelemetryWouldDenySummary(0, frozenset())
    label_ids = set(
        db.scalars(
            select(DataAccessEnvelopeLabel.label_id)
            .select_from(model)
            .join(
                DataAccessEnvelope,
                and_(
                    DataAccessEnvelope.resource_type == resource_type,
                    DataAccessEnvelope.resource_id == model.id,
                ),
            )
            .join(
                DataAccessEnvelopeLabel,
                DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
            )
            .join(
                HandlingLabel,
                HandlingLabel.id == DataAccessEnvelopeLabel.label_id,
            )
            .where(
                *filters,
                denied,
                or_(
                    DataAccessEnvelopeLabel.label_id.not_in(enforced.allowed_label_ids),
                    HandlingLabel.is_active.is_(False),
                ),
            )
            .distinct()
        ).all()
    )
    inaccessible_label = exists(
        select(DataAccessEnvelopeLabel.envelope_id)
        .select_from(DataAccessEnvelope)
        .join(
            DataAccessEnvelopeLabel,
            DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
        )
        .join(
            HandlingLabel,
            HandlingLabel.id == DataAccessEnvelopeLabel.label_id,
        )
        .where(
            DataAccessEnvelope.resource_type == resource_type,
            DataAccessEnvelope.resource_id == model.id,
            or_(
                DataAccessEnvelopeLabel.label_id.not_in(enforced.allowed_label_ids),
                HandlingLabel.is_active.is_(False),
            ),
        )
    )
    unexplained_denial = db.scalar(
        select(model.id).where(*filters, denied, ~inaccessible_label)
    )
    if unexplained_denial is not None:
        label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    return AITelemetryWouldDenySummary(affected_count, frozenset(label_ids))


def _is_system_run(run: AITaskRun) -> bool:
    return bool(
        run.task_type == _SYSTEM_TASK_TYPE
        and run.item_id is None
        and run.daily_brief_id is None
        and run.report_id is None
        and run.parent_run_id is None
    )


def _resource_envelope_exists(resource_type: str, resource_id_column):
    envelope = aliased(DataAccessEnvelope)
    return exists(
        select(envelope.id).where(
            envelope.resource_type == resource_type,
            envelope.resource_id == resource_id_column,
        )
    )


def _is_system_usage(
    event: AIUsageEvent,
    *,
    run: AITaskRun | None,
    task_run_id: uuid.UUID | None,
) -> bool:
    if (
        event.feature_type != _SYSTEM_TASK_TYPE
        or event.item_id is not None
        or event.daily_brief_id is not None
        or event.report_id is not None
    ):
        return False
    if task_run_id is None:
        return True
    return bool(
        run is not None
        and run.data_access_scope == AI_DATA_ACCESS_SCOPE_SYSTEM
        and run.data_access_lineage_complete
        and _is_system_run(run)
    )


def _task_run_shape_is_provable(run: AITaskRun) -> bool:
    if run.task_type == _DIRECT_TASK_TYPE_ITEM:
        return bool(
            run.item_id is not None
            and run.daily_brief_id is None
            and run.report_id is None
        )
    if run.task_type == _DIRECT_TASK_TYPE_DAILY_BRIEF:
        return bool(
            run.item_id is None
            and run.daily_brief_id is not None
            and run.report_id is None
        )
    if run.task_type == _DIRECT_TASK_TYPE_REPORT:
        return bool(
            run.item_id is None
            and run.daily_brief_id is None
            and run.report_id is not None
        )
    if run.task_type == "reprocess":
        return bool(
            run.item_id is None and run.daily_brief_id is None and run.report_id is None
        )
    return False


def _usage_event_shape_is_provable(event: AIUsageEvent) -> bool:
    if event.feature_type == _DIRECT_TASK_TYPE_ITEM:
        return bool(
            event.item_id is not None
            and event.daily_brief_id is None
            and event.report_id is None
        )
    if event.feature_type == _DIRECT_TASK_TYPE_DAILY_BRIEF:
        return bool(
            event.item_id is None
            and event.daily_brief_id is not None
            and event.report_id is None
        )
    if event.feature_type == _DIRECT_TASK_TYPE_REPORT:
        return bool(
            event.item_id is None
            and event.daily_brief_id is None
            and event.report_id is not None
        )
    return False


__all__ = [
    "AI_DATA_ACCESS_SCOPE_GOVERNED",
    "AI_DATA_ACCESS_SCOPE_SYSTEM",
    "AITelemetryWouldDenySummary",
    "ai_audit_history_access_predicate",
    "ai_audit_history_would_deny_summary",
    "ai_task_run_access_predicate",
    "ai_task_run_would_deny_summary",
    "ai_usage_event_access_predicate",
    "ai_usage_event_would_deny_summary",
    "ai_overview_source_would_deny_summary",
    "capture_ai_task_run_data_access",
    "capture_ai_usage_event_data_access",
    "cancel_ai_task_run_for_data_access",
    "complete_ai_task_run_data_access",
    "get_ai_task_run_detail_for_data_access",
    "initialize_ai_task_run_data_access",
    "list_ai_task_runs_for_data_access",
    "taint_ai_audit_history_for_feed",
]
