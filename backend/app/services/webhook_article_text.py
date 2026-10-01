"""Opt-in article text with bounded reads and immutable source references."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.item import Item
from app.schemas.notification import NotificationWebhookWrite

MAX_ARTICLE_TEXT_BYTES = 128 * 1024
ARTICLE_TEXT_SNAPSHOT_KEY = "webhook_article_text_snapshot"
_ARTICLE_VARIABLE = re.compile(
    r"\{\{\s*item\.(?:full_text(?:_status|_characters|_included_bytes)?|source_revision|article_retrieved_at)\s*\}\}"
)
_FULL_TEXT_VARIABLE = re.compile(r"\{\{\s*item\.full_text\s*\}\}")


@dataclass(frozen=True)
class ArticleTextSnapshot:
    text: str = ""
    status: Literal["available", "truncated", "unavailable", "source_changed"] = (
        "unavailable"
    )
    source_revision: int | None = None
    article_id: str | None = None
    article_retrieved_at: str | None = None
    original_characters: int = 0
    included_bytes: int = 0


def _template_fragments(payload: NotificationWebhookWrite) -> list[str]:
    fragments = [payload.url_template, payload.body_template or ""]
    for fields in (payload.headers, payload.query_params, payload.body_fields):
        fragments.extend(part for field in fields for part in (field.key, field.value))
    return fragments


def uses_article_text(payload: NotificationWebhookWrite) -> bool:
    return any(
        _ARTICLE_VARIABLE.search(fragment) for fragment in _template_fragments(payload)
    )


def uses_snapshot_context(payload: NotificationWebhookWrite) -> bool:
    return uses_article_text(payload) or any(
        re.search(
            r"\{\{\s*ai\.(?:relevance_score|relevance_label|generated_at|summary|summary_truncated|relevance_reasons|relevance_reasons_truncated)\s*\}\}",
            fragment,
        )
        for fragment in _template_fragments(payload)
    )


def validate_article_text_placement(payload: NotificationWebhookWrite) -> None:
    fragments = [payload.url_template]
    for fields in (payload.headers, payload.query_params):
        fragments.extend(part for field in fields for part in (field.key, field.value))
    fragments.extend(field.key for field in payload.body_fields)
    if any(_FULL_TEXT_VARIABLE.search(fragment) for fragment in fragments):
        raise ValueError(
            "Article full text can only be included in request body values"
        )


def capture_article_reference(db: Session, item_id: uuid.UUID) -> dict | None:
    row = db.execute(
        select(
            Item.classification_required_version,
            Article.id,
            Article.retrieved_at,
            Article.content_purged_at,
        )
        .select_from(Item)
        .outerjoin(Article, Article.item_id == Item.id)
        .where(Item.id == item_id)
    ).one_or_none()
    if row is None:
        return None
    return {
        "source_revision": row.classification_required_version,
        "article_id": str(row.id) if row.id else None,
        "article_retrieved_at": row.retrieved_at.isoformat()
        if row.retrieved_at
        else None,
        "article_content_purged_at": row.content_purged_at.isoformat()
        if row.content_purged_at
        else None,
    }


def add_article_reference(db: Session, *, payload: dict) -> None:
    """Retain only small source metadata in the shared event, never the body."""
    try:
        item_id = uuid.UUID(str(payload.get("item_id")))
    except (ValueError, TypeError):
        return
    if "source_revision" in payload and "article_id" in payload:
        reference = {
            key: payload.get(key)
            for key in (
                "source_revision",
                "article_id",
                "article_retrieved_at",
                "article_content_purged_at",
            )
        }
    else:
        reference = capture_article_reference(db, item_id)
    payload["article_text_reference"] = reference
    if isinstance(payload.get("item"), dict):
        payload["item"]["article_text_reference"] = reference


def load_article_text(
    db: Session, *, item_id: uuid.UUID, reference: dict | None
) -> ArticleTextSnapshot:
    """One SQL statement pins the version while returning at most 128 Ki characters.

    The final UTF-8 cap is smaller for multibyte text. Neither a whole Article ORM
    object nor an unbounded body is loaded, including on missing/stale sources.
    """
    if not isinstance(reference, dict):
        return ArticleTextSnapshot()
    try:
        revision = int(reference["source_revision"])
        article_id = reference.get("article_id")
        retrieved_at = reference.get("article_retrieved_at")
        result = ArticleTextSnapshot(
            source_revision=revision,
            article_id=article_id,
            article_retrieved_at=retrieved_at,
        )
        if (
            not article_id
            or not retrieved_at
            or reference.get("article_content_purged_at")
        ):
            return result
        article_uuid = uuid.UUID(article_id)
        retrieved_time = datetime.fromisoformat(retrieved_at)
    except (ValueError, TypeError, KeyError):
        return ArticleTextSnapshot()
    row = db.execute(
        select(
            func.substr(Article.text, 1, MAX_ARTICLE_TEXT_BYTES).label("text"),
            func.length(Article.text).label("characters"),
        )
        .join(Item, Item.id == Article.item_id)
        .where(
            Item.id == item_id,
            Item.classification_required_version == revision,
            Article.id == article_uuid,
            Article.retrieved_at == retrieved_time,
            Article.content_purged_at.is_(None),
        )
    ).one_or_none()
    if row is None:
        return replace(result, status="source_changed")
    text = (
        (row.text or "")
        .encode("utf-8")[:MAX_ARTICLE_TEXT_BYTES]
        .decode("utf-8", errors="ignore")
    )
    characters = row.characters or 0
    return replace(
        result,
        text=text,
        status="truncated"
        if characters > len(text)
        else "available"
        if text
        else "unavailable",
        original_characters=characters,
        included_bytes=len(text.encode("utf-8")),
    )


def article_text_for_item(
    db: Session, *, item, payload: NotificationWebhookWrite
) -> ArticleTextSnapshot:
    if not uses_article_text(payload) or item is None:
        return ArticleTextSnapshot()
    reference = getattr(item, "article_text_reference", None)
    if isinstance(item, Item) and not hasattr(item, "article_text_reference"):
        reference = capture_article_reference(db, item.id)
    return load_article_text(db, item_id=item.id, reference=reference)


def automation_payload_with_article_text(
    db: Session, *, event, payload: dict, include: bool
) -> dict:
    """Honor opt-in and the existing envelope cap, including JSON escaping costs."""
    from app.services.webhook_automation import automation_envelope

    result = dict(payload)
    result.pop("article_text", None)
    if not include:
        return result
    try:
        item_id = uuid.UUID(str(payload.get("item_id")))
    except (ValueError, TypeError):
        snapshot = ArticleTextSnapshot()
    else:
        snapshot = load_article_text(
            db, item_id=item_id, reference=payload.get("article_text_reference")
        )
    result["article_text"] = asdict(snapshot)
    try:
        automation_envelope(event, payload=result)
        return result
    except ValueError:
        pass
    # Binary search includes JSON escaping costs in the remaining event budget.
    original = snapshot.text
    low, high = 0, len(original)
    best = None
    while low <= high:
        length = (low + high) // 2
        text = original[:length]
        result["article_text"] = asdict(
            replace(
                snapshot,
                text=text,
                status="truncated"
                if len(text) < snapshot.original_characters
                else snapshot.status,
                included_bytes=len(text.encode("utf-8")),
            )
        )
        try:
            automation_envelope(event, payload=result)
        except ValueError:
            high = length - 1
        else:
            best = dict(result["article_text"])
            low = length + 1
    if best is None:
        raise ValueError("Automation metadata exceeds the bounded payload limit")
    result["article_text"] = best
    return result
