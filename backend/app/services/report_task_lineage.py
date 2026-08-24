from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.ai_ops_common import AI_TASK_TYPE_REPORT


MAX_REPORT_TASK_LINEAGE_DEPTH = 16


class ReportTaskLineageError(RuntimeError):
    pass


def resolve_report_task_run(
    db: Session,
    run: AITaskRun,
    *,
    lock: bool = False,
) -> AITaskRun:
    """Follow typed supersession links, with metadata compatibility for v1 rows."""

    _validate_report_run(run, report_id=run.report_id)
    current = run
    visited: set[uuid.UUID] = set()
    for _depth in range(MAX_REPORT_TASK_LINEAGE_DEPTH):
        if current.id in visited:
            raise ReportTaskLineageError(
                f"Report task supersession contains a cycle at run {current.id}."
            )
        visited.add(current.id)
        replacement_id = _replacement_id(current)
        if replacement_id is None:
            return current
        query = select(AITaskRun).where(AITaskRun.id == replacement_id)
        if lock:
            query = query.with_for_update()
        replacement = db.scalar(query.execution_options(populate_existing=True))
        if replacement is None:
            raise ReportTaskLineageError(
                f"Report task run {current.id} refers to missing replacement {replacement_id}."
            )
        _validate_report_run(replacement, report_id=current.report_id)
        current = replacement
    raise ReportTaskLineageError(
        f"Report task supersession exceeds {MAX_REPORT_TASK_LINEAGE_DEPTH} links."
    )


def find_report_request_task_run(
    db: Session,
    *,
    report: Report,
) -> AITaskRun | None:
    """Resolve the canonical task for the request that created the report."""

    run = (
        db.get(AITaskRun, report.request_task_run_id)
        if report.request_task_run_id is not None
        else None
    )
    if run is None:
        return None
    _validate_report_run(run, report_id=report.id)
    return resolve_report_task_run(db, run)


def _replacement_id(run: AITaskRun) -> uuid.UUID | None:
    if run.superseded_by_task_run_id is not None:
        return run.superseded_by_task_run_id
    raw_id = (run.metadata_json or {}).get("superseded_by_task_run_id")
    if not isinstance(raw_id, str):
        return None
    try:
        return uuid.UUID(raw_id)
    except ValueError as exc:
        raise ReportTaskLineageError(
            f"Report task run {run.id} has an invalid replacement identifier."
        ) from exc


def _validate_report_run(
    run: AITaskRun,
    *,
    report_id: uuid.UUID | None,
) -> None:
    if (
        report_id is None
        or run.report_id != report_id
        or run.task_type != AI_TASK_TYPE_REPORT
    ):
        raise ReportTaskLineageError(
            f"Report task run {run.id} refers to an unrelated replacement."
        )


__all__ = [
    "ReportTaskLineageError",
    "find_report_request_task_run",
    "resolve_report_task_run",
]
