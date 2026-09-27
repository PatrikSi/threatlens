"""Explicit continuation budgets, source identity and accepting-credential fences."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_article_continuation import AIArticleContinuation
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_egress_data_policy import AIEgressPolicyError, lock_ai_egress_policy_fence
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_job_access import authorize_export_job, ExportJobAccessDenied

MAX_AUTHORIZED_SECTIONS = 32
MAX_AUTHORIZED_TOKENS = 256_000


def progress_digest(progress: dict) -> str:
    return hashlib.sha256(json.dumps(progress, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recover_unsent_sections(db: Session, *, item_id: uuid.UUID, progress: dict) -> dict:
    """Prepare a new authorized run without replaying an uncertain old request.

    The prior run is terminal before acceptance; its not-sent receipt outcomes
    cannot be superseded by a resumed old worker after the new run takes over.
    Keep both their audit history and conservative token reservations; the new
    run reserves its own calls. Missing receipts are not proof of non-delivery.
    This check runs both at acceptance and before the worker changes progress.
    """
    recovered = copy.deepcopy(progress)
    started = [section for section in recovered["sections"] if section["status"] == "started"]
    synthesis = recovered.get("synthesis") or {}
    recovering_synthesis = synthesis.get("status") in {"started", "failed"}
    if not started and not recovering_synthesis:
        return recovered
    try:
        prior_run_id = uuid.UUID(progress["task_run_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("The previous section task is unavailable. Review its provider receipts.") from exc
    prior_run = db.execute(select(AITaskRun.status, AITaskRun.item_id, AITaskRun.metadata_json).where(AITaskRun.id == prior_run_id)).one_or_none()
    if prior_run is None or prior_run.item_id != item_id or prior_run.status not in {"ready", "error", "skipped"}:
        raise ValueError("The previous section task is not settled. Wait for it to finish before continuing.")
    receipts = list(db.scalars(select(AIProviderAttemptReceipt).where(
        AIProviderAttemptReceipt.task_run_id_snapshot == prior_run_id,
        AIProviderAttemptReceipt.feature_type == "item_enrichment",
        AIProviderAttemptReceipt.resource_type == "item",
        AIProviderAttemptReceipt.resource_id == item_id,
    )))
    for section in started:
        matching = [receipt for receipt in receipts
                    if receipt.request_fingerprint == section.get("request_fingerprint")]
        if not matching or any(receipt.state not in {"failed", "voided"}
                               or receipt.io_outcome != "not_sent" for receipt in matching):
            raise ValueError("A section delivery is unresolved. Reconcile its provider receipt before continuing.")
        section.update(status="pending", previous_attempt_receipts=[str(receipt.id) for receipt in matching])
    if recovering_synthesis:
        # Older checkpoints did not store a fingerprint until synthesis succeeded.
        # The operation identity still binds their receipts to this exact stage.
        root_id = (prior_run.metadata_json or {}).get("provider_operation_root_id")
        try:
            operation_id = uuid.uuid5(uuid.UUID(root_id), f"item_section_synthesis:{len(synthesis['completed_sections'])}") if root_id else None
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("The previous synthesis task is unavailable. Review its provider receipts.") from exc
        matching = [receipt for receipt in receipts if (
            receipt.request_fingerprint == synthesis.get("request_fingerprint")
            if synthesis.get("request_fingerprint") else receipt.operation_id == operation_id
        )]
        if not matching or any(receipt.state not in {"failed", "voided"}
                               or receipt.io_outcome != "not_sent" for receipt in matching):
            raise ValueError("Synthesis delivery is unresolved. Reconcile its provider receipt before retrying synthesis.")
        synthesis.update(status="pending", previous_attempt_receipts=[str(receipt.id) for receipt in matching])
    return recovered


def continuation_authority(db: Session, *, run_id: uuid.UUID | None) -> AIArticleContinuation | None:
    if run_id is None:
        return None
    row = db.get(AIArticleContinuation, run_id)
    if row is None:
        return None
    lock_ai_egress_policy_fence(db)
    try:
        _, access = authorize_export_job(db, row, lock=True, required_permissions=("read:items", "write:ai"))
        visible = db.scalar(select(Item.id).join(Feed, Feed.id == Item.feed_id).where(
            Item.id == row.item_id, handling_label_access_predicate(Feed.handling_label_id, access)))
        if visible is None:
            raise ExportJobAccessDenied("Article access changed")
    except ExportJobAccessDenied as exc:
        raise AIEgressPolicyError(
            "The credential authorizing additional article sections is no longer valid or permitted. "
            "Sign in, review current evidence and authorize continuation again.", retryable=False,
        ) from exc
    return row


def continuation_preflight_error(db: Session, run_id: uuid.UUID | None, active, article_text: str) -> str | None:
    if run_id is None:
        return None
    from app.models.item_ai_enrichment import ItemAIEnrichment
    run = db.get(AITaskRun, run_id)
    progress = db.scalar(select(ItemAIEnrichment.extraction_progress_json).where(
        ItemAIEnrichment.item_id == run.item_id)) if run and run.item_id else None
    owns_plan = isinstance(progress, dict) and progress.get("task_run_id") == str(run_id)
    if not owns_plan and db.get(AIArticleContinuation, run_id) is None:
        return None
    if not active.structured_extraction_enabled:
        return "Structured extraction was disabled after section processing was accepted. Review current settings before reprocessing."
    if len(" ".join(article_text.split())) <= 8000:
        return "The article changed after section processing was accepted. Refresh its evidence before reprocessing."
    return None
