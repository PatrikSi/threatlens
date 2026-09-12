"""Replay report stage completions committed atomically with provider receipts."""

import uuid
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReportStageArtifact
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError

COMPLETION_FIELDS = (
    "payload", "provider", "model", "latency_ms", "prompt_tokens",
    "completion_tokens", "total_tokens", "attempt_count", "prompt_char_count",
    "response_char_count",
)


def _failure(message: str, code: str = "report_resume_conflict") -> AIIntegrationError:
    error = AIIntegrationError(message, retryable=False, provider_io_outcome="not_sent")
    error.code = code
    error.attempt_count = 0
    return error


def _validate_binding(db, *, task_run_id, report_id):
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == task_run_id)
                    .with_for_update().execution_options(populate_existing=True))
    if run is None or run.task_type != "report" or run.report_id != report_id:
        raise _failure("Saved report stage does not match the requested task and report.")
    if (run.metadata_json or {}).get("cancel_requested_at"):
        raise _failure("Report generation was canceled.", "canceled")
    if run.status != "running" or run.finished_at is not None:
        raise _failure("Report generation task is no longer active.", "task_stopped")


def load_report_stage_completion(
    db: Session, *, task_run_id: uuid.UUID, report_id: uuid.UUID,
    operation_scope: str, request_fingerprint: str,
) -> AICompletionResult | None:
    _validate_binding(db, task_run_id=task_run_id, report_id=report_id)
    artifact = db.get(AIReportStageArtifact, (task_run_id, operation_scope))
    if artifact is None:
        return None
    if artifact.report_id != report_id or artifact.request_fingerprint != request_fingerprint:
        raise _failure("Report inputs changed after this stage completed. Start a new report to use the changed inputs.")
    try:
        values = artifact.completion_json
        if not isinstance(values, dict) or set(values) != set(COMPLETION_FIELDS):
            raise ValueError("Invalid saved completion fields")
        if not isinstance(values["payload"], dict):
            raise ValueError("Invalid saved completion payload")
        for field in ("provider", "model"):
            if not isinstance(values[field], str):
                raise ValueError("Invalid saved provider identity")
        for field in COMPLETION_FIELDS[3:]:
            value = values[field]
            if value is not None and (type(value) is not int or value < 0 or value > 2_147_483_647):
                raise ValueError("Invalid saved usage count")
        return AICompletionResult(**values)
    except (TypeError, ValueError) as exc:
        raise _failure("Saved report stage output is unavailable; automatic provider replay was blocked.") from exc


def store_report_stage_completion(
    db: Session, *, task_run_id: uuid.UUID, report_id: uuid.UUID,
    operation_scope: str, request_fingerprint: str, completion: AICompletionResult,
) -> None:
    _validate_binding(db, task_run_id=task_run_id, report_id=report_id)
    existing = db.get(AIReportStageArtifact, (task_run_id, operation_scope))
    values = asdict(completion)
    payload = {field: values[field] for field in COMPLETION_FIELDS}
    if existing is not None:
        if (existing.report_id != report_id or existing.request_fingerprint != request_fingerprint
                or existing.completion_json != payload):
            raise _failure("A different completion already exists for this report stage.")
        return
    db.add(AIReportStageArtifact(
        task_run_id=task_run_id, report_id=report_id, operation_scope=operation_scope,
        request_fingerprint=request_fingerprint, completion_json=payload,
    ))
    db.flush()


def has_report_stage_completion(db: Session, *, task_run_id: uuid.UUID, operation_scope: str) -> bool:
    """Read-only hint; authorize first, then load and validate under the task lock."""
    return db.scalar(select(AIReportStageArtifact.task_run_id).where(
        AIReportStageArtifact.task_run_id == task_run_id,
        AIReportStageArtifact.operation_scope == operation_scope,
    )) is not None
