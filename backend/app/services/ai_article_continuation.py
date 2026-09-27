"""Explicit continuation budgets, source identity and accepting-credential fences."""
from __future__ import annotations

import hashlib
import json
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_article_continuation import AIArticleContinuation
from app.models.feed import Feed
from app.models.item import Item
from app.services.ai_egress_data_policy import AIEgressPolicyError, lock_ai_egress_policy_fence
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_job_access import authorize_export_job, ExportJobAccessDenied

MAX_AUTHORIZED_SECTIONS = 32
MAX_AUTHORIZED_TOKENS = 256_000


def progress_digest(progress: dict) -> str:
    return hashlib.sha256(json.dumps(progress, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    if run_id is None or db.get(AIArticleContinuation, run_id) is None:
        return None
    if not active.structured_extraction_enabled:
        return "Structured extraction was disabled after continuation was authorized. Review current settings before reprocessing."
    if len(" ".join(article_text.split())) <= 8000:
        return "The article changed after continuation was authorized. Refresh its evidence before reprocessing."
    return None
