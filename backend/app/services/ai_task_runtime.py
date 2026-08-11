from __future__ import annotations

import ast
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.schemas.ai import AILiveStatusResponse, AILiveTaskResponse
from app.services.ai_ops_common import (
    AI_STATUS_QUEUED,
    AI_STATUS_RUNNING,
    AI_TASK_NAMES,
    _coerce_utc,
    _extract_uuid,
)
from app.tasks.celery_app import celery_app


def get_ai_db_live_status(
    db: Session, *, active_task_limit: int = 4
) -> AILiveStatusResponse:
    status_counts = {
        status: int(count)
        for status, count in db.execute(
            select(AITaskRun.status, func.count(AITaskRun.id))
            .where(AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]))
            .group_by(AITaskRun.status)
        ).all()
    }
    oldest_queued = db.scalar(
        select(AITaskRun.queued_at)
        .where(AITaskRun.status == AI_STATUS_QUEUED)
        .order_by(AITaskRun.queued_at.asc())
    )
    oldest_age = None
    if oldest_queued is not None:
        oldest_age = max(
            0,
            int(
                (
                    datetime.now(timezone.utc) - _coerce_utc(oldest_queued)
                ).total_seconds()
            ),
        )

    active_runs = list(
        db.scalars(
            select(AITaskRun)
            .where(AITaskRun.status == AI_STATUS_RUNNING)
            .order_by(
                AITaskRun.started_at.desc().nullslast(), AITaskRun.updated_at.desc()
            )
            .limit(active_task_limit)
        )
    )
    workers = sorted({run.worker_name for run in active_runs if run.worker_name})
    active_tasks = [
        AILiveTaskResponse(
            worker_name=run.worker_name or "database",
            celery_task_id=run.celery_task_id,
            task_name=run.task_type,
            state="active",
            run_id=run.id,
            item_id=run.item_id,
            parent_run_id=run.parent_run_id,
            received_at=run.started_at.isoformat() if run.started_at else None,
            raw_name=None,
        )
        for run in active_runs
    ]

    return AILiveStatusResponse(
        worker_count=len(workers),
        workers=workers,
        active_tasks=active_tasks,
        reserved_tasks=[],
        scheduled_tasks=[],
        active_count=status_counts.get(AI_STATUS_RUNNING, 0),
        reserved_count=0,
        scheduled_count=0,
        queued_count=status_counts.get(AI_STATUS_QUEUED, 0),
        oldest_queued_age_seconds=oldest_age,
    )


def _load_live_task_snapshot() -> tuple[
    bool,
    list[str],
    list[AILiveTaskResponse],
    list[AILiveTaskResponse],
    list[AILiveTaskResponse],
]:
    settings = get_settings()
    try:
        inspector = celery_app.control.inspect(
            timeout=settings.health_worker_ping_timeout_seconds
        )
    except Exception:
        return False, [], [], [], []

    responses: list[dict[str, Any]] = []
    all_calls_succeeded = True
    for inspect_call in (
        inspector.ping,
        inspector.active,
        inspector.reserved,
        inspector.scheduled,
    ):
        try:
            response = inspect_call() or {}
        except Exception:
            response = {}
            all_calls_succeeded = False
        responses.append(response)

    _ping, active_raw, reserved_raw, scheduled_raw = responses
    worker_names = set().union(*(response.keys() for response in responses))
    workers = sorted(worker_names)
    snapshot_complete = (
        bool(workers)
        and all_calls_succeeded
        and all(set(response.keys()) == worker_names for response in responses)
    )
    return (
        snapshot_complete,
        workers,
        _flatten_live_tasks(active_raw, state="active"),
        _flatten_live_tasks(reserved_raw, state="reserved"),
        _flatten_live_tasks(scheduled_raw, state="scheduled"),
    )


def _normalize_live_task_snapshot(
    snapshot: tuple[
        bool,
        list[str],
        list[AILiveTaskResponse],
        list[AILiveTaskResponse],
        list[AILiveTaskResponse],
    ]
    | tuple[
        list[str],
        list[AILiveTaskResponse],
        list[AILiveTaskResponse],
        list[AILiveTaskResponse],
    ],
) -> tuple[
    bool,
    list[str],
    list[AILiveTaskResponse],
    list[AILiveTaskResponse],
    list[AILiveTaskResponse],
]:
    if len(snapshot) == 4:
        workers, active_tasks, reserved_tasks, scheduled_tasks = snapshot
        return True, workers, active_tasks, reserved_tasks, scheduled_tasks
    return snapshot


def _flatten_live_tasks(
    raw_tasks: dict[str, list[dict[str, Any]]], *, state: str
) -> list[AILiveTaskResponse]:
    entries: list[AILiveTaskResponse] = []
    for worker_name, tasks in raw_tasks.items():
        for raw in tasks or []:
            name = raw.get("name")
            if name not in AI_TASK_NAMES:
                continue
            kwargs = raw.get("kwargs") or {}
            request = raw.get("request") or {}
            args = _coerce_live_args(raw.get("args") or request.get("args"))
            task_run_id = _extract_uuid(
                kwargs.get("task_run_id")
                or request.get("kwargs", {}).get("task_run_id")
                or _extract_positional_task_run_id(name, args)
            )
            item_id = _extract_uuid(
                kwargs.get("item_id") or _extract_positional_item_id(name, args)
            )
            parent_run_id = _extract_uuid(kwargs.get("parent_run_id"))
            eta_value = raw.get("eta")
            received_value = (
                raw.get("time_start") or raw.get("received") or raw.get("acknowledged")
            )
            entries.append(
                AILiveTaskResponse(
                    worker_name=worker_name,
                    celery_task_id=raw.get("id") or request.get("id"),
                    task_name=AI_TASK_NAMES[name],
                    state=state,
                    run_id=task_run_id,
                    item_id=item_id,
                    parent_run_id=parent_run_id,
                    eta=str(eta_value) if eta_value is not None else None,
                    received_at=str(received_value)
                    if received_value is not None
                    else None,
                    raw_name=name,
                )
            )
    return entries


def _coerce_live_args(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return []
        if isinstance(parsed, (list, tuple)):
            return list(parsed)
    return []


def _extract_positional_task_run_id(task_name: str, args: list[Any]) -> Any:
    if (
        task_name == "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation"
        and len(args) > 1
    ):
        return args[1]
    if (
        task_name == "app.tasks.feed_tasks.generate_item_ai_enrichment"
        and len(args) > 2
    ):
        return args[2]
    if task_name == "app.tasks.feed_tasks.reprocess_recent_ai_items" and len(args) > 6:
        return args[6]
    if task_name == "app.tasks.feed_tasks.backfill_daily_ai_briefs" and len(args) > 1:
        return args[1]
    return None


def _extract_positional_item_id(task_name: str, args: list[Any]) -> Any:
    if task_name == "app.tasks.feed_tasks.generate_item_ai_enrichment" and args:
        return args[0]
    return None
