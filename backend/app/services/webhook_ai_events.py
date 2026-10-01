"""Immutable shared AI relevance and revision-fenced completion notifications.

Team assessments are deliberately absent: they carry a different audience and
context version. Relevance belongs to the successful shared article result.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.ai_enrichment_provenance import current_enrichment_predicate

AI_READY_EVENT = "article.ai.ready"
RELEVANCE_EVENT_TYPES = frozenset({
    "rss_item_new", "alert_match", "intel.extraction.ready",
    "intel.indicators.changed", "hunt.approved", AI_READY_EVENT,
})


def condition_uses_ai_relevance(conditions: dict | None) -> bool:
    if not isinstance(conditions, dict):
        return False
    pending = [conditions]
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if node.get("field") in {"ai_relevance_score", "ai_relevance_label"}:
            return True
        children = node.get("conditions")
        if isinstance(children, list):
            pending.extend(children)
    return False


def condition_values_with_current_ai(
    db: Session, *, payload: dict, created_at: datetime, event_type: str,
    conditions: WebhookConditionGroup | None, lock: bool = False,
) -> dict:
    """An outdated optional AI field becomes unknown without changing OR semantics."""
    from app.services.webhook_conditions import event_condition_values

    if conditions is not None and condition_uses_ai_relevance(conditions.model_dump()):
        if not ai_event_current(db, payload, lock=lock):
            payload = {**payload, "ai_relevance": None}
    return event_condition_values(payload, created_at=created_at, event_type=event_type)


def relevance_snapshot_values(payload: dict) -> dict:
    """Unknown, malformed and legacy unverified relevance never satisfy NOT."""
    snapshot = payload.get("ai_relevance")
    available = isinstance(snapshot, dict) and snapshot.get("verified") is True
    proof = snapshot.get("provenance") if available else None
    available = (
        available and isinstance(proof, dict) and proof.get("version") == 1
        and proof.get("source_hash") == snapshot.get("source_hash")
        and bool(snapshot.get("source_hash"))
        and proof.get("source_version") == payload.get("source_revision")
        and proof.get("article_id") == payload.get("article_id")
    )
    score = snapshot.get("score") if available else None
    label = snapshot.get("label") if available else None
    if not (
        isinstance(score, (int, float)) and not isinstance(score, bool)
        and math.isfinite(score) and 0 <= score <= 1
    ):
        score = None
    if not isinstance(label, str) or label not in {"low", "medium", "high"}:
        label = None
    return {"ai_relevance_score": score, "ai_relevance_label": label}


def current_relevance_snapshot(
    db: Session, item_id: uuid.UUID, *, include_text: bool = False,
) -> dict | None:
    """One bounded SQL projection verifies the result and its complete inputs."""
    text_columns = (
        func.left(ItemAIEnrichment.summary_text, 8000).label("summary"),
        (func.char_length(ItemAIEnrichment.summary_text) > 8000).label("summary_truncated"),
        case((func.octet_length(cast(ItemAIEnrichment.relevance_reasons_json, String)) <= 8192,
              ItemAIEnrichment.relevance_reasons_json), else_=None).label("reasons"),
    ) if include_text else ()
    proof = ItemAIEnrichment.result_provenance_json
    row = db.execute(
        select(
            ItemAIEnrichment.relevance_score, ItemAIEnrichment.relevance_label,
            ItemAIEnrichment.generated_at, ItemAIEnrichment.source_hash,
            func.json_build_object(
                "version", proof["version"], "source_hash", proof["source_hash"],
                "source_version", proof["source_version"], "article_id", proof["article_id"],
            ).label("proof"),
            func.encode(func.sha256(func.convert_to(cast(proof, String), "UTF8")), "hex").label("proof_digest"),
            Article.retrieved_at,
            *text_columns,
        )
        .select_from(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .join(Article, Article.item_id == Item.id)
        .join(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == item_id, Article.content_purged_at.is_(None),
               current_enrichment_predicate())
    ).one_or_none()
    if row is None or row.generated_at is None:
        return None
    snapshot = {
        "verified": True, "score": row.relevance_score,
        "label": row.relevance_label, "generated_at": row.generated_at.isoformat(),
        "source_hash": row.source_hash, "provenance": row.proof,
        "provenance_digest": row.proof_digest,
        "article_retrieved_at": row.retrieved_at.isoformat(),
    }
    if include_text:
        reasons = row.reasons if isinstance(row.reasons, list) else []
        valid_reasons = [value for value in reasons if isinstance(value, str)]
        snapshot.update(
            summary=row.summary, summary_truncated=bool(row.summary_truncated),
            relevance_reasons=[value[:500] for value in valid_reasons[:4]],
            relevance_reasons_truncated=(
                not isinstance(row.reasons, list) or len(valid_reasons) != len(reasons)
                or len(reasons) > 4 or any(len(value) > 500 for value in valid_reasons)
            ),
        )
    return snapshot


def capture_relevance_metadata(db: Session, *, event_type: str, payload: dict) -> None:
    if event_type not in RELEVANCE_EVENT_TYPES:
        return
    try:
        item_id = uuid.UUID(str(payload.get("item_id")))
    except (ValueError, TypeError):
        return
    snapshot = current_relevance_snapshot(db, item_id, include_text=event_type == AI_READY_EVENT)
    payload["ai_relevance"] = snapshot
    if snapshot is None:
        return
    proof = snapshot["provenance"]
    # An event already pinned to another revision must not inherit newer AI.
    if (
        payload.get("source_revision", proof["source_version"]) != proof["source_version"]
        or payload.get("article_id", proof["article_id"]) != proof["article_id"]
    ):
        payload["ai_relevance"] = None
        return
    payload.setdefault("source_revision", proof["source_version"])
    payload.setdefault("article_id", proof["article_id"])
    payload.setdefault("article_retrieved_at", snapshot["article_retrieved_at"])
    item = payload.get("item")
    if isinstance(item, dict):
        item["ai_relevance"] = snapshot


def emit_article_ai_ready(db: Session, *, item_id: uuid.UUID) -> uuid.UUID | None:
    """Publish once per accepted result even when the indicator set is unchanged."""
    snapshot = current_relevance_snapshot(db, item_id, include_text=True)
    if snapshot is None:
        return None
    row = db.execute(
        select(
            func.substr(Item.title, 1, 512).label("title"),
            func.substr(Item.url, 1, 2048).label("url"),
            func.substr(Item.canonical_url, 1, 2048).label("canonical_url"),
            func.substr(Item.summary, 1, 8000).label("summary"), Item.status,
            Item.feed_id, Item.classification_required_version,
            Article.id.label("article_id"), Article.retrieved_at,
            func.substr(Feed.name, 1, 512).label("feed_name"),
            case(
                (func.octet_length(Feed._url_encrypted) <= 16384, Feed._url_encrypted),
                else_="",
            ).label("feed_url_encrypted"),
        )
        .join(Article, Article.item_id == Item.id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(Item.id == item_id)
    ).one()
    # Recheck by the common capture hook before persistence. A concurrent source
    # update can suppress this historical event, never upgrade its evidence.
    from app.services.integration_events import emit_integration_event
    from app.services.feed_storage import try_decrypt_feed_url
    from app.services.url_utils import redact_feed_url

    identity = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    key = f"{AI_READY_EVENT}:{item_id}:{identity}"
    feed_url, _error = try_decrypt_feed_url(row.feed_url_encrypted)
    event = emit_integration_event(
        db, event_type=AI_READY_EVENT, source_type="item", source_id=item_id,
        idempotency_key=key, schema_version=1,
        payload={
            "schema_version": 1, "item_id": str(item_id), "feed_id": str(row.feed_id),
            "source_revision": row.classification_required_version,
            "article_id": str(row.article_id),
            "article_retrieved_at": row.retrieved_at.isoformat(),
            "ai_relevance": snapshot, "action_id": hashlib.sha256(key.encode()).hexdigest(),
            "item": {"id": str(item_id), "title": row.title[:512],
                     "url": row.url[:2048], "canonical_url": (row.canonical_url or "")[:2048],
                     "summary": (row.summary or "")[:8000], "status": row.status},
            "feed": {"id": str(row.feed_id), "name": row.feed_name[:512],
                     "url": redact_feed_url(feed_url or "")[:2048]},
        },
    )
    return event.id


def ai_event_current(db: Session, payload: dict, *, lock: bool = False) -> bool:
    """Reject replaced/failed AI and refreshed sources before routing or sending.

    NOWAIT avoids lock inversion with a provider worker that already owns its
    enrichment row. The caller retains successful share locks during delivery.
    """
    try:
        item_id = uuid.UUID(payload["item_id"])
        snapshot = payload["ai_relevance"]
        if not isinstance(snapshot, dict) or snapshot.get("verified") is not True:
            return False
        expected_retrieved = datetime.fromisoformat(payload["article_retrieved_at"])
    except (ValueError, KeyError, TypeError):
        return False
    try:
        with db.begin_nested():
            if lock:
                for model, key in ((Item, Item.id), (Article, Article.item_id),
                                   (ItemAIEnrichment, ItemAIEnrichment.item_id)):
                    db.execute(select(key).where(key == item_id).with_for_update(
                        read=True, nowait=True, of=model,
                    )).all()
            source = db.execute(select(
                Item.classification_required_version, Article.id, Article.retrieved_at,
            ).join(Article, Article.item_id == Item.id).where(Item.id == item_id)).one_or_none()
            if source is None or (
                source.classification_required_version != payload.get("source_revision")
                or str(source.id) != payload.get("article_id")
                or source.retrieved_at != expected_retrieved
            ):
                return False
            return current_relevance_snapshot(db, item_id, include_text="summary" in snapshot) == snapshot
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code in {"55P03", "40P01", "40001"}:
            from app.services.intel_event_eligibility import IntelEventBusy

            raise IntelEventBusy("Article AI evidence is changing; retry delivery shortly.") from exc
        raise
