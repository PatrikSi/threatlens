"""Automatic repair resumes accepted work; it never authorizes another plan."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services.ai_extraction_sections import checkpointed_receipt_fingerprints
from app.services.ai_workflow_recovery import recover_stale_workflow

MAX_AUTOMATIC_RECOVERIES = 3


def recover_failed_enrichment(db: Session, *, item_id: UUID) -> UUID | None:
    """Keep the task identity, provider, authority and paid-call reservations.

    Terminal receipt failures are deliberately left for explicit operator action.
    A fresh plan is only available through deliberate reprocessing. Legacy errors
    without durable task history provide no proof that a request was not sent.
    """
    from app.services.data_access_runtime import lock_data_policy_revision_for_derivation
    from app.services.ai_ops import record_ai_task_event

    lock_data_policy_revision_for_derivation(db)
    run = db.scalar(select(AITaskRun).where(
        AITaskRun.item_id == item_id, AITaskRun.task_type == "item_enrichment",
    ).order_by(AITaskRun.created_at.desc(), AITaskRun.id.desc()).limit(1)
        .with_for_update().execution_options(populate_existing=True))
    resource = db.get(ItemAIEnrichment, item_id)
    if run is None or resource is None or resource.status != "error" or run.status != "error":
        return None
    metadata = dict(run.metadata_json or {})
    attempts = int(metadata.get("automatic_recovery_count", 0))
    if (metadata.get("cancel_requested_at") or metadata.get("automatic_recovery_blocked")
            or run.parent_run_id is not None or attempts >= MAX_AUTOMATIC_RECOVERIES
            or (resource.extraction_progress_json is not None
                and checkpointed_receipt_fingerprints(resource, run_id=run.id) is None)):
        recovered = None
    else:
        recovered = recover_stale_workflow(db, run)
    if recovered != "guarded":
        run.metadata_json = {**metadata, "automatic_recovery_blocked": True}
        db.add(run)
        return None
    now = datetime.now(timezone.utc)
    run.finished_at = None
    run.error = None
    run.queued_at = now
    run.updated_at = now
    run.metadata_json = {**dict(run.metadata_json or {}), "automatic_recovery_count": attempts + 1}
    record_ai_task_event(db, run_id=run.id, event_type="automatic_recovery_queued", payload={
        "attempt": attempts + 1, "maximum": MAX_AUTOMATIC_RECOVERIES,
        "preserves_extraction_plan": True,
    })
    db.add(run)
    return run.id
