from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.services.ai_ops_common import (
    AI_TASK_TYPE_REPORT,
    AI_TASK_TYPE_REPORT_SUPERSEDED,
)


MAX_REPORT_TASK_LINEAGE_DEPTH = 16


class ReportTaskLineageError(RuntimeError):
    pass


def resolve_report_task_run(
    db: Session,
    run: AITaskRun,
) -> AITaskRun:
    """Follow typed supersession links, with metadata compatibility for v1 rows."""

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
        replacement = db.get(AITaskRun, replacement_id)
        if replacement is None:
            raise ReportTaskLineageError(
                f"Report task run {current.id} refers to missing replacement {replacement_id}."
            )
        if replacement.report_id != current.report_id or replacement.task_type not in {
            AI_TASK_TYPE_REPORT,
            AI_TASK_TYPE_REPORT_SUPERSEDED,
        }:
            raise ReportTaskLineageError(
                f"Report task run {current.id} refers to an unrelated replacement."
            )
        current = replacement
    raise ReportTaskLineageError(
        f"Report task supersession exceeds {MAX_REPORT_TASK_LINEAGE_DEPTH} links."
    )


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


__all__ = [
    "ReportTaskLineageError",
    "resolve_report_task_run",
]
