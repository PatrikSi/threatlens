"""Return an owned report task to its durable dispatcher after zero-I/O deferral."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.report_execution import release_report_generation


def defer_report_workflow(db, *, report_id, run_id, lease_token, generation_fence,
                          reason, retry_after_seconds):
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    report = db.get(Report, report_id)
    if (run is None or report is None or run.report_id != report_id
            or run.finished_at is not None or run.status != "running"
            or (run.metadata_json or {}).get("cancel_requested_at")):
        db.rollback()
        return {"status": "skipped", "reason": "task_stopped"}
    if not release_report_generation(db, report_id=report_id, lease_token=lease_token,
                                     generation_fence=generation_fence):
        db.rollback()
        return {"status": "skipped", "reason": "ownership_lost"}
    run.status, run.worker_name, run.reason = "queued", None, None
    run.dispatch_claim_token = run.dispatch_claim_expires_at = None
    run.dispatch_published_at = None
    run.dispatch_next_attempt_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(1, min(3600, retry_after_seconds)))
    run.dispatch_error = reason
    run.metadata_json = {**dict(run.metadata_json or {}), "deferred_reason": reason}
    report.status, report.generation_stage = "queued", "waiting_for_capacity"
    db.add_all([run, report])
    db.commit()
    return {"status": "queued", "reason": reason}
