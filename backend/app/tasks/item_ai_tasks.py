import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import ModuleType

from sqlalchemy import func, select

from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.item import Item
from app.services.ai_telemetry_data_policy import capture_ai_task_run_data_access


@dataclass(frozen=True)
class AIReprocessSelection:
    days: int | None
    requested_limit: int
    effective_limit: int
    start_time: datetime | None
    end_time: datetime | None
    feed_ids: list[uuid.UUID]
    item_ids: list[uuid.UUID]
    requested_item_count: int
    cutoff: datetime | None

    @property
    def truncated_item_count(self) -> int:
        return max(0, self.requested_item_count - len(self.item_ids))


def run_generate_item_ai_enrichment(
    task,
    item_id: str,
    force: bool = False,
    task_run_id: str | None = None,
    *,
    runtime: ModuleType,
):
    r = runtime
    with r.db_session() as db:
        parsed_run_id = _parse_uuid(task_run_id)
        if parsed_run_id:
            start_result = _start_item_run(
                db, task, parsed_run_id, item_id, force, runtime=r
            )
            if start_result is not None:
                return start_result

        parsed_item_id = _parse_uuid(item_id)
        if parsed_item_id is None:
            _finish_invalid_item_run(db, task, parsed_run_id, runtime=r)
            return {
                "status": "skipped",
                "reason": "invalid_item_id",
                "item_id": item_id,
            }

        _claimed_item, claim_reason = r._claim_item_ai_enrichment_target(
            db, item_id=parsed_item_id
        )
        if claim_reason is not None:
            _finish_skipped_item_run(db, task, parsed_run_id, claim_reason, runtime=r)
            return {"status": "skipped", "reason": claim_reason, "item_id": item_id}
        db.commit()

        try:
            result = r.run_item_ai_enrichment(
                db, item_id=parsed_item_id, force=force, task_run_id=parsed_run_id
            )
        except Exception:
            db.rollback()
            _finish_unexpected_item_error(db, task, parsed_run_id, runtime=r)
            r.logger.exception(
                "AI enrichment task failed unexpectedly for item %s", item_id
            )
            return {"status": "error", "reason": "unexpected_error", "item_id": item_id}
        if parsed_run_id:
            _finish_item_result(db, task, parsed_run_id, result, runtime=r)
        db.commit()
        if result.enrichment is None:
            return {
                "status": "skipped",
                "reason": result.reason or "not_eligible",
                "item_id": item_id,
            }
        return {"status": result.status, "reason": result.reason, "item_id": item_id}


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def parse_uuid_text_list(values: list[str] | None) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in values or []:
        candidate = _parse_uuid(str(raw))
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        parsed.append(candidate)
    return parsed


def parse_datetime_text(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _start_item_run(
    db, task, run_id: uuid.UUID, item_id: str, force: bool, *, runtime: ModuleType
):
    r = runtime
    started_run = r.start_ai_task_run(
        db,
        run_id=run_id,
        worker_name=getattr(task.request, "hostname", None),
        celery_task_id=getattr(task.request, "id", None),
        metadata_updates={"force": bool(force)},
    )
    db.commit()
    if not r._task_run_claimed_by_current_worker(
        started_run, celery_task_id=getattr(task.request, "id", None)
    ):
        return {"status": "skipped", "reason": "already_running", "item_id": item_id}
    stop_reason = r.ai_task_run_stop_reason(started_run)
    if stop_reason is None:
        return None
    if stop_reason == "canceled":
        r.finish_ai_task_run(
            db,
            run_id=run_id,
            status=r.AI_STATUS_SKIPPED,
            reason="canceled",
            worker_name=getattr(task.request, "hostname", None),
            metadata_updates={
                "cancel_observed_at": datetime.now(timezone.utc).isoformat()
            },
        )
        db.commit()
    return {"status": "skipped", "reason": stop_reason, "item_id": item_id}


def _finish_invalid_item_run(
    db, task, run_id: uuid.UUID | None, *, runtime: ModuleType
) -> None:
    if run_id is None:
        return
    runtime.finish_ai_task_run(
        db,
        run_id=run_id,
        status=runtime.AI_STATUS_SKIPPED,
        reason="invalid_item_id",
        worker_name=getattr(task.request, "hostname", None),
    )
    db.commit()


def _finish_skipped_item_run(
    db,
    task,
    run_id: uuid.UUID | None,
    reason: str,
    *,
    runtime: ModuleType,
) -> None:
    if run_id is None:
        return
    runtime.finish_ai_task_run(
        db,
        run_id=run_id,
        status=runtime.AI_STATUS_SKIPPED,
        reason=reason,
        worker_name=getattr(task.request, "hostname", None),
    )
    db.commit()


def _finish_unexpected_item_error(
    db, task, run_id: uuid.UUID | None, *, runtime: ModuleType
) -> None:
    if run_id is None:
        return
    runtime.finish_ai_task_run(
        db,
        run_id=run_id,
        status=runtime.AI_STATUS_ERROR,
        reason="unexpected_error",
        error="unexpected_error",
        worker_name=getattr(task.request, "hostname", None),
    )
    db.commit()


def _finish_item_result(
    db, task, run_id: uuid.UUID, result, *, runtime: ModuleType
) -> None:
    enrichment = result.enrichment
    runtime.finish_ai_task_run(
        db,
        run_id=run_id,
        status=(
            runtime.AI_STATUS_READY
            if result.status == "ready"
            else runtime.AI_STATUS_ERROR
            if result.status == "error"
            else runtime.AI_STATUS_SKIPPED
        ),
        reason=result.reason,
        error=enrichment.error
        if enrichment is not None and result.status == "error"
        else None,
        worker_name=getattr(task.request, "hostname", None),
        model=enrichment.model if enrichment is not None else None,
        prompt_tokens=enrichment.prompt_tokens if enrichment is not None else None,
        completion_tokens=enrichment.completion_tokens
        if enrichment is not None
        else None,
        total_tokens=enrichment.total_tokens if enrichment is not None else None,
        latency_ms=enrichment.latency_ms if enrichment is not None else None,
        prompt_char_count=result.prompt_char_count,
        response_char_count=result.response_char_count,
        input_text_chars=result.input_text_chars,
        metadata_updates={
            "summary_available": bool(enrichment.summary_text)
            if enrichment is not None
            else False,
            "relevance_label": enrichment.relevance_label
            if enrichment is not None
            else None,
        },
    )


def run_reprocess_recent_ai_items(
    task,
    days: int | None,
    limit: int,
    start_time: str | None = None,
    end_time: str | None = None,
    feed_ids: list[str] | None = None,
    item_ids: list[str] | None = None,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
    *,
    runtime: ModuleType,
):
    r = runtime
    selection = _build_selection(
        days, limit, start_time, end_time, feed_ids, item_ids, runtime=r
    )
    parsed_run_id = _parse_uuid(task_run_id)
    parsed_actor_user_id = _parse_uuid(actor_user_id)
    with r.db_session() as db:
        start_result = _start_reprocess_run(
            db, task, parsed_run_id, task_run_id, selection, runtime=r
        )
        if start_result is not None:
            return start_result
        active_ai_settings, unavailable_result = _load_reprocess_ai_settings(
            db, task, parsed_run_id, runtime=r
        )
        if unavailable_result is not None:
            return unavailable_result
        selected_item_ids = _select_item_ids(db, selection)
        _record_selection(db, parsed_run_id, selected_item_ids, selection, runtime=r)
        if not selected_item_ids:
            _finish_empty_selection(db, task, parsed_run_id, selection, runtime=r)
            return {"queued": 0, "reason": "no_items"}

    return _queue_selected_items(
        task,
        selected_item_ids,
        selection,
        parsed_run_id,
        parsed_actor_user_id,
        active_ai_settings.model,
        task_run_id,
        runtime=r,
    )


def _build_selection(
    days: int | None,
    limit: int,
    start_time: str | None,
    end_time: str | None,
    feed_ids: list[str] | None,
    item_ids: list[str] | None,
    *,
    runtime: ModuleType,
) -> AIReprocessSelection:
    effective_limit = max(
        1, min(int(limit), int(runtime.get_settings().dispatch_ai_reprocess_batch_size))
    )
    parsed_start_time = parse_datetime_text(start_time)
    parsed_end_time = parse_datetime_text(end_time)
    parsed_feed_ids = parse_uuid_text_list(feed_ids)
    parsed_item_ids = parse_uuid_text_list(item_ids)
    requested_item_count = len(parsed_item_ids)
    parsed_item_ids = parsed_item_ids[:effective_limit]
    cutoff = None
    if parsed_start_time is None and parsed_end_time is None and not parsed_item_ids:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 7)))
    return AIReprocessSelection(
        days=days,
        requested_limit=int(limit),
        effective_limit=effective_limit,
        start_time=parsed_start_time,
        end_time=parsed_end_time,
        feed_ids=parsed_feed_ids,
        item_ids=parsed_item_ids,
        requested_item_count=requested_item_count,
        cutoff=cutoff,
    )


def _start_reprocess_run(
    db,
    task,
    run_id: uuid.UUID | None,
    task_run_id: str | None,
    selection: AIReprocessSelection,
    *,
    runtime: ModuleType,
):
    if run_id is None:
        return None
    r = runtime
    started_run = r.start_ai_task_run(
        db,
        run_id=run_id,
        worker_name=getattr(task.request, "hostname", None),
        celery_task_id=getattr(task.request, "id", None),
        metadata_updates=_selection_metadata(selection, include_effective_limit=True),
    )
    db.commit()
    if not r._task_run_claimed_by_current_worker(
        started_run, celery_task_id=getattr(task.request, "id", None)
    ):
        return {
            "queued": 0,
            "queue_errors": 0,
            "run_id": task_run_id,
            "reason": "already_running",
        }
    stop_reason = r.ai_task_run_stop_reason(started_run)
    if stop_reason is None:
        return None
    if stop_reason == "canceled":
        r.finish_ai_task_run(
            db,
            run_id=run_id,
            status=r.AI_STATUS_SKIPPED,
            reason="canceled",
            worker_name=getattr(task.request, "hostname", None),
            metadata_updates={
                "cancel_observed_at": datetime.now(timezone.utc).isoformat()
            },
        )
        db.commit()
    return {"queued": 0, "reason": stop_reason}


def _selection_metadata(
    selection: AIReprocessSelection, *, include_effective_limit: bool
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "days": int(selection.days or 0) if selection.days is not None else None,
        "limit": selection.requested_limit,
        "start_time": selection.start_time.isoformat()
        if selection.start_time
        else None,
        "end_time": selection.end_time.isoformat() if selection.end_time else None,
        "feed_ids": [str(feed_id) for feed_id in selection.feed_ids],
        "explicit_item_count": len(selection.item_ids),
        "truncated_item_count": selection.truncated_item_count,
        "date_basis": "published_at_or_first_seen_at",
    }
    if include_effective_limit:
        metadata["effective_limit"] = selection.effective_limit
    return metadata


def _load_reprocess_ai_settings(
    db, task, run_id: uuid.UUID | None, *, runtime: ModuleType
):
    settings = runtime.load_active_ai_settings(db)
    reason = None
    if not settings.ai_enabled:
        reason = "ai_disabled"
    elif not settings.ai_configured:
        reason = "ai_not_configured"
    if reason is None:
        return settings, None
    if run_id:
        runtime.finish_ai_task_run(
            db,
            run_id=run_id,
            status=runtime.AI_STATUS_SKIPPED,
            reason=reason,
            worker_name=getattr(task.request, "hostname", None),
        )
        db.commit()
    return settings, {"queued": 0, "reason": reason}


def _select_item_ids(db, selection: AIReprocessSelection) -> list[uuid.UUID]:
    timeline = func.coalesce(Item.published_at, Item.first_seen_at)
    query = (
        select(Item.id)
        .join(Article, Article.item_id == Item.id)
        .where(Article.text.is_not(None))
    )
    if selection.item_ids:
        selected = set(db.scalars(query.where(Item.id.in_(selection.item_ids))).all())
        return [item_id for item_id in selection.item_ids if item_id in selected]
    if selection.cutoff is not None:
        query = query.where(timeline >= selection.cutoff)
    if selection.start_time is not None:
        query = query.where(timeline >= selection.start_time)
    if selection.end_time is not None:
        query = query.where(timeline <= selection.end_time)
    if selection.feed_ids:
        query = query.where(Item.feed_id.in_(selection.feed_ids))
    query = query.limit(selection.effective_limit)
    return list(
        db.scalars(query.order_by(timeline.desc(), Item.first_seen_at.desc())).all()
    )


def _record_selection(
    db,
    run_id: uuid.UUID | None,
    item_ids: list[uuid.UUID],
    selection: AIReprocessSelection,
    *,
    runtime: ModuleType,
) -> None:
    if run_id is None:
        return
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id))
    if run is None:
        return
    run.target_count = len(item_ids)
    db.add(run)
    db.flush()
    capture_ai_task_run_data_access(
        db,
        run_id=run_id,
        item_ids=item_ids,
        feed_ids=selection.feed_ids,
        complete=True,
    )
    runtime.record_ai_task_event(
        db,
        run_id=run_id,
        event_type="selection_complete",
        payload={
            "target_count": len(item_ids),
            **_selection_metadata(selection, include_effective_limit=True),
        },
    )
    db.commit()


def _finish_empty_selection(
    db,
    task,
    run_id: uuid.UUID | None,
    selection: AIReprocessSelection,
    *,
    runtime: ModuleType,
) -> None:
    if run_id is None:
        return
    runtime.finish_ai_task_run(
        db,
        run_id=run_id,
        status=runtime.AI_STATUS_SKIPPED,
        reason="no_items",
        worker_name=getattr(task.request, "hostname", None),
        metadata_updates=_selection_metadata(selection, include_effective_limit=False),
    )
    db.commit()


def _queue_selected_items(
    task,
    item_ids: list[uuid.UUID],
    selection: AIReprocessSelection,
    run_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    model: str,
    task_run_id: str | None,
    *,
    runtime: ModuleType,
):
    queued = 0
    queue_errors = 0
    for item_id in item_ids:
        stop_reason = runtime._get_ai_run_stop_reason(run_id)
        if stop_reason is not None:
            _record_queue_stop(
                task, run_id, stop_reason, queued, queue_errors, runtime=runtime
            )
            return {
                "queued": queued,
                "queue_errors": queue_errors,
                "run_id": task_run_id,
                "reason": stop_reason,
            }
        queued_ok = runtime._safe_queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=runtime.AI_TRIGGER_MANUAL,
            reason=None,
            actor_user_id=actor_user_id,
            parent_run_id=run_id,
            force=True,
            model=model,
            metadata=_child_metadata(selection),
        )
        if queued_ok:
            queued += 1
        else:
            queue_errors += 1
    _record_children_queued(run_id, queued, queue_errors, runtime=runtime)
    return {"queued": queued, "queue_errors": queue_errors, "run_id": task_run_id}


def _child_metadata(selection: AIReprocessSelection) -> dict[str, object]:
    return {
        "days": int(selection.days or 0) if selection.days is not None else None,
        "limit": selection.requested_limit,
        "parent_task": "reprocess",
        "start_time": selection.start_time.isoformat()
        if selection.start_time
        else None,
        "end_time": selection.end_time.isoformat() if selection.end_time else None,
        "feed_ids": [str(feed_id) for feed_id in selection.feed_ids],
        "explicit_item_count": len(selection.item_ids),
        "date_basis": "published_at_or_first_seen_at",
    }


def _record_queue_stop(
    task,
    run_id: uuid.UUID | None,
    reason: str,
    queued: int,
    queue_errors: int,
    *,
    runtime: ModuleType,
) -> None:
    if run_id is None:
        return
    with runtime.db_session() as db:
        runtime.record_ai_task_event(
            db,
            run_id=run_id,
            event_type="queueing_stopped",
            payload={"reason": reason, "queued": queued, "queue_errors": queue_errors},
        )
        if reason == "canceled":
            runtime.finish_ai_task_run(
                db,
                run_id=run_id,
                status=runtime.AI_STATUS_SKIPPED,
                reason="canceled",
                worker_name=getattr(task.request, "hostname", None),
                metadata_updates={
                    "queued": queued,
                    "queue_errors": queue_errors,
                    "cancel_observed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        db.commit()


def _record_children_queued(
    run_id: uuid.UUID | None,
    queued: int,
    queue_errors: int,
    *,
    runtime: ModuleType,
) -> None:
    if run_id is None:
        return
    with runtime.db_session() as db:
        runtime.record_ai_task_event(
            db,
            run_id=run_id,
            event_type="children_queued",
            payload={"queued": queued, "queue_errors": queue_errors},
        )
        db.commit()
