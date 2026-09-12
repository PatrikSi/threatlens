"""Immutable selection and one durable outcome per reprocessed article."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReprocessMember
from app.models.article import Article
from app.models.item import Item
from app.services.data_access_envelopes import get_data_access_envelope_sources
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation

TERMINAL = {"ready", "error", "skipped"}
PROGRESS_REPAIR_BATCH_SIZE = 100


def article_reprocess_parent(run: AITaskRun) -> bool:
    return run.task_type == "reprocess" and (run.metadata_json or {}).get("scope") != "daily_brief_backfill"


def _uuid_list(values) -> list[uuid.UUID]:
    result = []
    for value in values or []:
        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if parsed not in result:
            result.append(parsed)
    return result


def _date(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def freeze_reprocess_selection(db: Session, run: AITaskRun) -> list[AIReprocessMember]:
    if not article_reprocess_parent(run):
        return []
    if not (run.metadata_json or {}).get("selection_frozen"):
        lock_data_policy_revision_for_derivation(db)
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run.id).with_for_update()
                    .execution_options(populate_existing=True))
    metadata = dict(run.metadata_json or {})
    if metadata.get("selection_frozen"):
        return list(db.scalars(select(AIReprocessMember).where(
            AIReprocessMember.parent_run_id == run.id
        ).order_by(AIReprocessMember.position).execution_options(populate_existing=True)))
    children = list(db.scalars(select(AITaskRun).where(
        AITaskRun.parent_run_id == run.id, AITaskRun.task_type == "item_enrichment"
    ).order_by(AITaskRun.created_at, AITaskRun.id)))
    by_item = {}
    for child in children:
        if child.item_id is None:
            continue
        previous = by_item.get(child.item_id)
        if previous is None or (child.status == "ready" and previous.status != "ready"):
            by_item[child.item_id] = child
    sources = get_data_access_envelope_sources(
        db, resource_type="ai_task_run", resource_id=run.id
    ) if run.target_count is not None or children else ()
    inherited = _uuid_list([source.source_id for source in sources if source.source_type == "item"])
    selected = list(dict.fromkeys([*inherited, *by_item]))
    if not selected:
        selected = _select_items(db, run=run, metadata=metadata)
    members = []
    for position, item_id in enumerate(selected):
        child = by_item.get(item_id)
        settled = child is not None and child.status in TERMINAL and child.finished_at is not None
        member = AIReprocessMember(
            parent_run_id=run.id, item_id=item_id, position=position,
            child_run_id=child.id if child else None,
            outcome=child.status if settled else None,
            reason=child.reason if settled else None,
            settled_at=child.finished_at if settled else None,
        )
        db.add(member)
        members.append(member)
    run.metadata_json = {**metadata, "selection_frozen": True, "selection_version": 1}
    run.target_count = len(members)
    db.add(run)
    db.flush()
    from app.services.ai_telemetry_data_policy import capture_ai_task_run_data_access
    capture_ai_task_run_data_access(db, run_id=run.id, item_ids=selected, complete=True)
    return members


def _select_items(db: Session, *, run: AITaskRun, metadata: dict) -> list[uuid.UUID]:
    limit = max(1, int(metadata["effective_limit"])) if metadata.get("effective_limit") else max(
        1, min(int(metadata.get("limit") or 100), get_settings().dispatch_ai_reprocess_batch_size)
    )
    timeline = func.coalesce(Item.published_at, Item.first_seen_at)
    query = select(Item.id).join(Article, Article.item_id == Item.id).where(Article.text.is_not(None))
    explicit = _uuid_list(metadata.get("item_ids"))[:limit]
    if explicit:
        available = set(db.scalars(query.where(Item.id.in_(explicit))))
        return [item_id for item_id in explicit if item_id in available]
    start, end = _date(metadata.get("start_time")), _date(metadata.get("end_time"))
    if start is None and end is None:
        anchor = run.created_at or datetime.now(timezone.utc)
        start = anchor - timedelta(days=max(1, int(metadata.get("days") or 7)))
    if start is not None:
        query = query.where(timeline >= start)
    if end is not None:
        query = query.where(timeline <= end)
    feed_ids = _uuid_list(metadata.get("feed_ids"))
    if feed_ids:
        query = query.where(Item.feed_id.in_(feed_ids))
    return list(db.scalars(query.order_by(timeline.desc(), Item.first_seen_at.desc(), Item.id).limit(limit)))


def ensure_reprocess_child(
    db: Session, *, parent_id: uuid.UUID, item_id: uuid.UUID, model: str | None
) -> AITaskRun | None:
    from app.services.ai_ops import finish_ai_task_run, queue_ai_task_run
    lock_data_policy_revision_for_derivation(db)
    parent = db.scalar(select(AITaskRun).where(AITaskRun.id == parent_id).with_for_update()
                       .execution_options(populate_existing=True))
    if parent is None or not article_reprocess_parent(parent):
        raise ValueError("Article reprocessing parent is unavailable")
    from app.services.ai_execution_ownership import ai_execution_is_current
    if not ai_execution_is_current(parent):
        return None
    if not (parent.metadata_json or {}).get("selection_frozen"):
        freeze_reprocess_selection(db, parent)
    member = db.get(AIReprocessMember, (parent.id, item_id))
    if member is None:
        raise ValueError("Article is outside the accepted reprocessing selection")
    if member.child_run_id is not None:
        child = db.get(AITaskRun, member.child_run_id)
        if child is not None:
            return child
        if member.outcome is None:
            member.outcome, member.reason = "error", "child_history_unavailable"
            member.settled_at = datetime.now(timezone.utc)
            db.add(member)
            recalculate_reprocess_progress(db, parent=parent)
        return None
    if member.outcome is not None or parent.finished_at is not None:
        return None
    if (parent.metadata_json or {}).get("cancel_requested_at"):
        return None
    child = queue_ai_task_run(
        db, task_type="item_enrichment", trigger_source=parent.trigger_source,
        actor_user_id=parent.actor_user_id, item_id=item_id if db.get(Item, item_id) else None,
        parent_run_id=parent.id, model=model,
        metadata={"force": True, "parent_task": "reprocess", "accepted_item_id": str(item_id)},
    )
    member.child_run_id = child.id
    db.add(member)
    db.flush()
    if child.item_id is None:
        finish_ai_task_run(db, run_id=child.id, status="skipped", reason="item_not_found")
    return child


def record_reprocess_outcome(db: Session, *, child: AITaskRun, parent: AITaskRun) -> bool:
    if not article_reprocess_parent(parent):
        return False
    if not (parent.metadata_json or {}).get("selection_frozen"):
        freeze_reprocess_selection(db, parent)
    member = db.scalar(select(AIReprocessMember).where(
        AIReprocessMember.parent_run_id == parent.id, AIReprocessMember.child_run_id == child.id))
    if member is None and child.item_id is not None:
        member = db.get(AIReprocessMember, (parent.id, child.item_id))
        if member is not None and member.child_run_id is None:
            member.child_run_id = child.id
    if member is not None and member.child_run_id == child.id and member.outcome is None:
        member.outcome, member.reason, member.settled_at = child.status, child.reason, child.finished_at
        db.add(member)
        db.flush()
    recalculate_reprocess_progress(db, parent=parent)
    return True


def _repair_reprocess_outcomes(db: Session, *, parent_id: uuid.UUID) -> None:
    missing = AITaskRun.id.is_(None)
    candidates = select(
        AIReprocessMember.item_id,
        case((missing, "error"), else_=AITaskRun.status).label("outcome"),
        case((missing, "child_history_unavailable"), else_=AITaskRun.reason).label("reason"),
        func.coalesce(AITaskRun.finished_at, func.now()).label("settled_at"),
    ).outerjoin(AITaskRun, AITaskRun.id == AIReprocessMember.child_run_id).where(
        AIReprocessMember.parent_run_id == parent_id, AIReprocessMember.outcome.is_(None),
        AIReprocessMember.child_run_id.is_not(None),
        or_(missing, AITaskRun.status.in_(TERMINAL) & AITaskRun.finished_at.is_not(None)),
    ).order_by(AIReprocessMember.item_id).limit(PROGRESS_REPAIR_BATCH_SIZE).with_for_update(
        of=AIReprocessMember, skip_locked=True).cte("reprocess_outcome_repair")
    db.execute(update(AIReprocessMember).where(
        AIReprocessMember.parent_run_id == parent_id,
        AIReprocessMember.item_id == candidates.c.item_id,
    ).values(outcome=candidates.c.outcome, reason=candidates.c.reason,
             settled_at=candidates.c.settled_at).execution_options(synchronize_session=False))


def recalculate_reprocess_progress(db: Session, *, parent: AITaskRun) -> None:
    from app.services.ai_ops import finish_ai_task_run
    from app.services.ai_ops_common import INELIGIBLE_REASONS
    from app.services.ai_execution_ownership import ai_execution_is_current

    lock_data_policy_revision_for_derivation(db)
    parent = db.scalar(select(AITaskRun).where(AITaskRun.id == parent.id).with_for_update()
                       .execution_options(populate_existing=True))
    if parent is None or not ai_execution_is_current(parent):
        return
    if not (parent.metadata_json or {}).get("selection_frozen"):
        freeze_reprocess_selection(db, parent)
    db.flush()
    _repair_reprocess_outcomes(db, parent_id=parent.id)
    member = AIReprocessMember
    counts = db.execute(select(
        func.count(), func.count().filter(member.outcome.is_not(None)),
        func.count().filter(member.outcome == "ready"), func.count().filter(member.outcome == "error"),
        func.count().filter(member.outcome == "skipped"),
        func.count().filter(member.reason.in_({"unchanged", "source_hash_unchanged"})),
        func.count().filter(member.reason.in_(INELIGIBLE_REASONS)),
    ).where(member.parent_run_id == parent.id)).one()
    (total, parent.processed_count, parent.success_count, parent.error_count, parent.skipped_count,
     parent.skipped_unchanged_count, parent.skipped_ineligible_count) = counts
    db.add(parent)
    if total and parent.processed_count == total and parent.finished_at is None:
        status = "error" if parent.error_count else "skipped" if parent.skipped_count else "ready"
        reason = "partial_failures" if parent.error_count else "partial_skips" if parent.skipped_count else None
        db.flush()
        finish_ai_task_run(db, run_id=parent.id, status=status, reason=reason)


def is_canonical_reprocess_child(db: Session, *, run_id: uuid.UUID) -> bool:
    child = db.get(AITaskRun, run_id)
    if child is None or child.parent_run_id is None:
        return True
    parent = db.get(AITaskRun, child.parent_run_id)
    if parent is None or not article_reprocess_parent(parent):
        return True
    if not (parent.metadata_json or {}).get("selection_frozen"):
        freeze_reprocess_selection(db, parent)
    return db.scalar(select(AIReprocessMember.item_id).where(
        AIReprocessMember.parent_run_id == parent.id, AIReprocessMember.child_run_id == child.id)) is not None


def finish_reprocess_publication(db: Session, *, run_id: uuid.UUID) -> None:
    from app.services.ai_workflow_dispatch import complete_workflow_dispatch, defer_ai_workflow_run
    from app.services.ai_execution_ownership import fence_ai_execution
    if not fence_ai_execution(db, run_id=run_id):
        return
    parent = db.get(AITaskRun, run_id)
    if parent is None or not article_reprocess_parent(parent):
        return
    if not (parent.metadata_json or {}).get("selection_frozen"):
        freeze_reprocess_selection(db, parent)
    pending = db.scalar(select(AIReprocessMember.item_id).where(
        AIReprocessMember.parent_run_id == run_id, AIReprocessMember.child_run_id.is_(None),
        AIReprocessMember.outcome.is_(None)).limit(1))
    if pending is None:
        complete_workflow_dispatch(db, run_id)
        recalculate_reprocess_progress(db, parent=parent)
    else:
        defer_ai_workflow_run(db, run_id=run_id, reason="child_publication_pending", retry_after_seconds=30)
