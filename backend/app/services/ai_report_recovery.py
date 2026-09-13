"""Resume interrupted reports only when durable receipts account for every stage."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIReportStageArtifact
from app.models.report import Report
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_report_stage_artifacts import load_report_stage_completion
from app.services.report_execution import fence_report_generation

REPORT_STAGE_PROTOCOL_KEY = 'report_stage_protocol'


def report_has_safe_resume_history(db, *, run: AITaskRun, report: Report) -> bool:
    if (run.task_type != 'report' or run.report_id != report.id or run.finished_at is not None
            or run.status != 'running' or run.superseded_by_task_run_id is not None
            or (run.metadata_json or {}).get('cancel_requested_at')
            or report.status not in {'running', 'queued'}):
        return False
    receipts = list(db.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id)))
    # Any uncertain operation for this report still blocks same-run recovery.
    unsafe = db.scalar(select(AIProviderAttemptReceipt.id).where(
        AIProviderAttemptReceipt.resource_type == 'report',
        AIProviderAttemptReceipt.resource_id == report.id,
        AIProviderAttemptReceipt.state.in_(['reserved', 'ambiguous']),
    ).limit(1))
    if unsafe is not None:
        return False
    if not receipts:
        artifact_exists = db.scalar(select(AIReportStageArtifact.task_run_id).where(
            AIReportStageArtifact.task_run_id == run.id).limit(1)) is not None
        return (not artifact_exists and int(report.model_calls or 0) == 0
                and (run.metadata_json or {}).get(REPORT_STAGE_PROTOCOL_KEY) == 1)
    try:
        operation_root = uuid.UUID(str((run.metadata_json or {}).get('provider_operation_root_id')))
    except (TypeError, ValueError):
        return False
    saved: dict[uuid.UUID, int] = {}
    artifacts = db.execute(select(
        AIReportStageArtifact.report_id, AIReportStageArtifact.operation_scope,
        AIReportStageArtifact.request_fingerprint,
    ).where(AIReportStageArtifact.task_run_id == run.id).execution_options(yield_per=50))
    try:
        for artifact in artifacts:
            if artifact.report_id != report.id:
                return False
            try:
                completion = load_report_stage_completion(db, task_run_id=run.id, report_id=report.id,
                    operation_scope=artifact.operation_scope, request_fingerprint=artifact.request_fingerprint)
            except AIIntegrationError:
                return False
            if completion is None:
                return False
            saved[uuid.uuid5(operation_root, artifact.operation_scope)] = completion.attempt_count
            # Validation still examines the complete saved stage. Retain only
            # its receipt evidence before loading the next potentially large body.
            del completion
    finally:
        artifacts.close()
    successful = set()
    attempts_by_operation = {}
    for receipt in receipts:
        if (receipt.feature_type != 'report' or receipt.resource_type != 'report'
                or receipt.resource_id != report.id or receipt.settled_at is None):
            return False
        accounted = receipt.attempt_number - (receipt.state == 'voided')
        attempts_by_operation[receipt.operation_id] = max(attempts_by_operation.get(receipt.operation_id, 0), accounted)
        if receipt.state == 'succeeded':
            if saved.get(receipt.operation_id) != receipt.attempt_number:
                return False
            successful.add(receipt.operation_id)
        elif receipt.state == 'failed':
            if not receipt.retryable or receipt.attempt_number >= receipt.max_attempts:
                return False
        elif receipt.state != 'voided':
            return False
    return (set(saved) == successful
            and int(report.model_calls or 0) <= sum(attempts_by_operation.values()))


def prepare_owned_report_resume(db, *, run_id, report_id, lease_token, generation_fence,
                                lease_seconds) -> bool:
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    if run is None or not fence_report_generation(db, report_id=report_id, lease_token=lease_token,
            generation_fence=generation_fence, lease_seconds=lease_seconds):
        db.rollback()
        return False
    report = db.get(Report, report_id)
    if report is None or not report_has_safe_resume_history(db, run=run, report=report):
        db.rollback()
        return False
    report.status, report.generation_stage = 'queued', 'resuming'
    run.metadata_json = {**dict(run.metadata_json or {}), 'resumed_after_worker_loss': True}
    db.add_all([report, run])
    db.commit()
    return True


def requeue_interrupted_report(db, *, run: AITaskRun, report: Report) -> bool:
    """Caller has locked the run and invalidated the expired generation lease."""
    if not report_has_safe_resume_history(db, run=run, report=report):
        return False
    run.status, run.worker_name, run.reason = 'queued', None, None
    run.dispatch_claim_token = run.dispatch_claim_expires_at = None
    run.dispatch_published_at = None
    run.dispatch_next_attempt_at = datetime.now(timezone.utc)
    run.dispatch_error = None
    run.metadata_json = {**dict(run.metadata_json or {}), 'worker_recovery_requested': True}
    report.status, report.generation_stage = 'queued', 'resuming'
    db.add_all([run, report])
    return True
