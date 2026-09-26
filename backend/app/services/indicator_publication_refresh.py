"""Monotonic withdrawals; reapproval creates a new indicator revision identity."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from sqlalchemy import delete, select, tuple_
from sqlalchemy.orm import Session, load_only

from app.models.article import Article
from app.models.feed import Feed
from app.models.indicator_publication import IndicatorPublication
from app.models.intel_assessment import IndicatorAssessment, IndicatorSuppression, ItemIntelState
from app.models.item import Item
from app.services.intel_events import source_fingerprint


def refresh_publication(db: Session, row: IndicatorPublication) -> None:
    """Caller locks publication; no authorization, team or source locks follow.

    This can only withdraw previously released entries. It never revives them or
    copies new evidence, so a concurrent source mutation is repaired by the next
    bounded sweep without producing a new approved external action.
    """
    now = datetime.now(timezone.utc)
    snapshot = deepcopy(row.snapshot_json)
    entries = snapshot["indicators"]
    active = [entry for entry in entries if not entry.get("withdrawn_at")]
    item_ids = {uuid.UUID(entry["item_id"]) for entry in active}
    assessment_ids = {uuid.UUID(entry["assessment_id"]) for entry in active}
    reviews = {str(review.id): review for review in db.scalars(select(IndicatorAssessment).where(
        IndicatorAssessment.id.in_(assessment_ids),
    ).options(load_only(IndicatorAssessment.id, IndicatorAssessment.version, IndicatorAssessment.verdict,
                        IndicatorAssessment.expires_at, IndicatorAssessment.source_revision,
                        IndicatorAssessment.extraction_revision)))}
    sources = {str(source.item_id): source for source in db.execute(select(
        Item.id.label("item_id"), Item.classification_required_version,
        Feed.handling_label_id, ItemIntelState.revision, ItemIntelState.source_fingerprint,
        Article.id.label("article_id"), Article.retrieved_at, Article.content_purged_at,
    ).join(Feed, Feed.id == Item.feed_id)
      .outerjoin(ItemIntelState, ItemIntelState.item_id == Item.id)
      .outerjoin(Article, Article.item_id == Item.id)
      .where(Item.id.in_(item_ids)))}
    keys = [(entry["type"], entry["value"]) for entry in active]
    suppressed = set(db.execute(select(IndicatorSuppression.ioc_type, IndicatorSuppression.value_norm).where(
        IndicatorSuppression.team_id == row.team_id, IndicatorSuppression.active.is_(True),
        tuple_(IndicatorSuppression.ioc_type, IndicatorSuppression.value_norm).in_(keys),
        IndicatorSuppression.expires_at.is_(None) | (IndicatorSuppression.expires_at > now),
    )).all()) if keys else set()
    for entry in active:
        review, source = reviews.get(entry["assessment_id"]), sources.get(entry["item_id"])
        reason = None
        if review is None or review.version != entry["assessment_version"] or review.verdict != "malicious":
            reason = "review_changed"
        elif review.expires_at is not None and review.expires_at <= now:
            reason = "review_expired"
        elif source is None or source.revision != entry["extraction_revision"]:
            reason = "evidence_changed"
        elif str(source.handling_label_id) not in entry["label_ids"]:
            reason = "source_policy_changed"
        else:
            article = SimpleNamespace(id=source.article_id, retrieved_at=source.retrieved_at, content_purged_at=source.content_purged_at) if source.article_id else None
            if source.source_fingerprint != entry["source_fingerprint"] or source_fingerprint(source, article) != entry["source_fingerprint"]:
                reason = "evidence_changed"
            elif (entry["type"], entry["value"]) in suppressed:
                reason = "team_suppression"
        if reason:
            entry.update(withdrawn_at=now.isoformat(), withdrawal_reason=reason)
    count = sum(bool(entry.get("withdrawn_at")) for entry in entries)
    if count != row.withdrawn_count:
        snapshot["misp_timestamp"] = max(int(now.timestamp()), int(snapshot.get("misp_timestamp", row.created_at.timestamp())) + 1)
        row.snapshot_json = snapshot
        row.withdrawn_count = count
        row.status = "withdrawn" if count == len(entries) else "partially_withdrawn"
        row.revision += 1
        row.updated_at = now
    row.next_check_at = now + timedelta(minutes=5)


def reconcile_publications(db: Session, *, limit: int = 50) -> int:
    """Persist scan progress for unchanged records, without broad unbounded scans."""
    rows = db.scalars(select(IndicatorPublication).where(
        IndicatorPublication.status != "withdrawn",
        IndicatorPublication.next_check_at <= datetime.now(timezone.utc),
    ).order_by(IndicatorPublication.next_check_at, IndicatorPublication.id)
      .limit(min(max(1, limit), 50)).with_for_update(skip_locked=True)).all()
    for row in rows:
        refresh_publication(db, row)
    # A long withdrawal horizon gives polling consumers time to apply updates.
    # Active publications are never removed by this history cleanup.
    expired = select(IndicatorPublication.id).where(
        IndicatorPublication.status == "withdrawn",
        IndicatorPublication.updated_at < datetime.now(timezone.utc) - timedelta(days=180),
    ).order_by(IndicatorPublication.updated_at, IndicatorPublication.id).limit(10).with_for_update(skip_locked=True)
    db.execute(delete(IndicatorPublication).where(IndicatorPublication.id.in_(expired)))
    return len(rows)
