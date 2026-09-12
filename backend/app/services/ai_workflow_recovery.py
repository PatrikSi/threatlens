"""Adopt legacy accepted work and recover only operations safe to resume."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services.ai_workflow_dispatch import register_ai_workflow


def adopt_legacy_workflows(db, *, limit: int):
    from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
    lock_data_policy_revision_for_derivation(db)
    runs = db.scalars(select(AITaskRun).outerjoin(
        AIWorkflowDispatch, AIWorkflowDispatch.run_id == AITaskRun.id
    ).where(
        AITaskRun.task_type.in_(["item_enrichment", "daily_brief", "reprocess"]),
        AITaskRun.status.in_(["queued", "running"]), AITaskRun.finished_at.is_(None),
        AIWorkflowDispatch.run_id.is_(None),
        (AITaskRun.task_type != "daily_brief") | AITaskRun.parent_run_id.is_(None),
    ).order_by(AITaskRun.created_at, AITaskRun.id).limit(limit).with_for_update(of=AITaskRun, skip_locked=True))
    adopted = 0
    for run in runs:
        job = register_ai_workflow(db, run)
        if job is None:
            continue
        if run.celery_task_id:
            job.attempt_count = 1
            if run.status == "queued":
                job.state = "published"
        db.add(job)
        adopted += 1
    return adopted


def recover_stale_workflow(db, run: AITaskRun) -> str | None:
    """Caller has already checked worker liveness, age, cancellation and row lock."""
    from app.services.ai_ops import finish_ai_task_run
    from app.services.ai_reprocess import article_reprocess_parent, recalculate_reprocess_progress
    from app.services.ai_task_settlement import _provider_claim_matches
    if run.task_type == "report":
        return None
    job = db.get(AIWorkflowDispatch, run.id) or register_ai_workflow(db, run)
    if job is None:
        return None  # Inline backfill children belong to their parent's delivery.
    if article_reprocess_parent(run):
        recalculate_reprocess_progress(db, parent=run)
        if run.finished_at is not None:
            return "finished"
    if (run.task_type == "reprocess"
            and (run.metadata_json or {}).get("scope") == "daily_brief_backfill"):
        from app.services.ai_brief_recovery import backfill_recovery_is_blocked
        if backfill_recovery_is_blocked(db, run):
            finish_ai_task_run(
                db, run_id=run.id, status="error", reason="provider_recovery_blocked",
                error="Interrupted daily-brief provider work requires receipt reconciliation before retry.",
            )
            return "finished"
    resource = None
    if run.task_type == "item_enrichment" and run.item_id:
        resource = db.get(ItemAIEnrichment, run.item_id)
        resource_type, resource_id = "item_ai_enrichment", run.item_id
    elif run.task_type == "daily_brief" and run.daily_brief_id:
        resource = db.get(AIDailyBrief, run.daily_brief_id)
        resource_type, resource_id = "daily_brief", run.daily_brief_id
    if (resource is not None and resource.status == "ready"
            and isinstance((run.metadata_json or {}).get("provider_claim"), dict)
            and _provider_claim_matches(
        run, resource_type=resource_type, resource_id=resource_id,
        resource_updated_at=resource.updated_at,
    )):
        finish_ai_task_run(
            db, run_id=run.id, status="ready", reason="completion_recovered",
            model=resource.model, prompt_tokens=resource.prompt_tokens,
            completion_tokens=resource.completion_tokens, total_tokens=resource.total_tokens,
            latency_ms=resource.latency_ms,
        )
        return "finished"
    receipts = list(db.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == run.id
    ).order_by(AIProviderAttemptReceipt.attempt_number)))
    if not receipts and (run.metadata_json or {}).get("provider_claim"):
        return None  # Legacy provider work without a receipt has no safe replay proof.
    if any(receipt.state in {"reserved", "ambiguous", "succeeded"} for receipt in receipts):
        return None  # Existing settlement reports interruption; never replay paid I/O.
    if receipts and receipts[-1].state == "failed" and not receipts[-1].retryable:
        return None
    # A new delivery fences delayed messages from a worker judged lost. The
    # logical run and all its provider-operation identities remain unchanged.
    delivery_id = str(uuid.uuid4())
    run.celery_task_id = delivery_id
    run.status, run.reason, run.worker_name = "queued", None, None
    run.metadata_json = {**dict(run.metadata_json or {}), "worker_recovery_requested_at": datetime.now(timezone.utc).isoformat()}
    job.state, job.delivery_id = "pending", delivery_id
    job.attempt_count = 0
    job.published_at = None
    job.claim_token = job.claim_expires_at = None
    job.next_attempt_at = datetime.now(timezone.utc)
    job.error = "worker_recovery_pending"
    db.add(run)
    db.add(job)
    return "guarded"


def owns_pending_daily_brief(db, *, task_run_id, brief) -> bool:
    from app.services.ai_task_settlement import _provider_claim_matches
    run = db.get(AITaskRun, task_run_id) if task_run_id is not None else None
    return bool(run is not None and run.finished_at is None and run.status == "running"
        and not (run.metadata_json or {}).get("cancel_requested_at")
        and isinstance((run.metadata_json or {}).get("provider_claim"), dict)
        and _provider_claim_matches(run, resource_type="daily_brief", resource_id=brief.id,
                                    resource_updated_at=brief.updated_at))
