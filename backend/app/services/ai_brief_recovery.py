"""Prove whether an interrupted backfill date can resume without replaying I/O."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.services.ai_ops_common import AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY
from app.services.ai_task_settlement import _provider_claim_matches


def brief_attempt_is_settled(run: AITaskRun) -> bool:
    if run.finished_at is None or run.status not in {"ready", "error", "skipped"}:
        return False
    if (run.metadata_json or {}).get(AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY) is False:
        return False
    return not (run.reason and run.reason.startswith("stale_"))


def completed_brief_for_attempt(db: Session, run: AITaskRun) -> AIDailyBrief | None:
    if run.daily_brief_id is None:
        return None
    brief = db.get(AIDailyBrief, run.daily_brief_id)
    if (brief is None or brief.status != "ready"
            or not isinstance((run.metadata_json or {}).get("provider_claim"), dict)):
        return None
    if not _provider_claim_matches(
        run, resource_type="daily_brief", resource_id=brief.id,
        resource_updated_at=brief.updated_at,
    ):
        return None
    return brief


def safe_interrupted_brief_attempt(db: Session, run: AITaskRun) -> bool:
    if _attempt_canceled(run):
        return False
    if completed_brief_for_attempt(db, run) is not None:
        return True
    receipts = list(db.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id,
    )))
    if not receipts:
        return not (run.metadata_json or {}).get("provider_claim")
    # Supersession creates a new child. A known paid failure must not silently
    # acquire a fresh attempt budget, even if its original operation can retry.
    return all(
        receipt.state == "voided" or (
            receipt.state == "failed" and receipt.io_outcome == "not_sent" and receipt.retryable
        )
        for receipt in receipts
    )


def _attempt_canceled(run: AITaskRun) -> bool:
    return bool((run.metadata_json or {}).get("cancel_requested_at")) or run.reason in {"canceled", "cancel_requested"}


def backfill_recovery_is_blocked(db: Session, parent: AITaskRun) -> bool:
    children = db.scalars(select(AITaskRun).where(
        AITaskRun.parent_run_id == parent.id, AITaskRun.task_type == "daily_brief",
    ).execution_options(yield_per=100))
    # Stale reconciliation may have terminalized an inline child before its
    # parent is inspected; its receipt still constrains the parent's replay.
    try:
        return any(
            not brief_attempt_is_settled(child) and not safe_interrupted_brief_attempt(db, child)
            for child in children
        )
    finally:
        children.close()


def reconcile_interrupted_brief_attempts(
    db: Session, *, parent: AITaskRun, attempts: list[AITaskRun],
) -> str | None:
    from app.services.ai_ops import finish_ai_task_run

    if any(brief_attempt_is_settled(attempt) for attempt in attempts):
        return "ready"
    if any(recover_completed_brief_attempt(db, attempt) for attempt in attempts):
        return "ready"
    if any(not safe_interrupted_brief_attempt(db, attempt) for attempt in attempts):
        finish_ai_task_run(
            db, run_id=parent.id, status="error", reason="provider_recovery_blocked",
            error="Interrupted daily-brief provider work requires receipt reconciliation before retry.",
        )
        return "provider_recovery_blocked"
    return None


def recover_completed_brief_attempt(db: Session, run: AITaskRun) -> bool:
    from app.services.ai_ops import finish_ai_task_run
    from app.services.data_access_runtime import lock_data_policy_revision_for_derivation

    lock_data_policy_revision_for_derivation(db)
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run.id)
                    .with_for_update().execution_options(populate_existing=True))
    if run is None or _attempt_canceled(run):
        return False
    if run.daily_brief_id is not None:
        db.scalar(select(AIDailyBrief).where(AIDailyBrief.id == run.daily_brief_id)
                  .with_for_update().execution_options(populate_existing=True))
    brief = completed_brief_for_attempt(db, run)
    if brief is None:
        return False
    # This is completion recovery of the original child, including a child
    # previously marked stale; it never submits a replacement provider call.
    run.status = "running"
    run.reason = None
    run.error = None
    run.finished_at = None
    run.duration_ms = None
    db.add(run)
    db.flush()
    finish_ai_task_run(
        db, run_id=run.id, status="ready", reason="completion_recovered",
        model=brief.model, prompt_tokens=brief.prompt_tokens,
        completion_tokens=brief.completion_tokens, total_tokens=brief.total_tokens,
        latency_ms=brief.latency_ms,
        metadata_updates={AI_PARENT_PROGRESS_ELIGIBLE_METADATA_KEY: True},
    )
    return True
