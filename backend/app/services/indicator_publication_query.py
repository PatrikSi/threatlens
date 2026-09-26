"""Bounded, permission-filtered reviewed evidence projection for publication."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import uuid

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.orm import Session, defer

from app.core.api_errors import ApiHTTPException
from app.models.article import Article
from app.models.feed import Feed
from app.models.intel_assessment import IndicatorAssessment, IndicatorSuppression, ItemIntelState
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.schemas.exports import ArticleExportFilters
from app.services.data_access_policy import handling_label_access_predicate
from app.services.export_query import build_export_query_context, load_export_item_ids
from app.services.indicator_evidence import indicator_exclusion
from app.services.indicator_lineage import assessment_access_predicate, assessment_labels_by_id
from app.services.intel_events import source_fingerprint
from app.services.team_assessment_access import AssessmentRequest

MAX_ARTICLES = 100
MAX_INDICATORS = 250
MAX_SNAPSHOT_BYTES = 1024 * 1024
NETWORK_TYPES = {"domain", "ipv4", "ipv6", "url", "email", "hash_md5", "hash_sha1", "hash_sha256"}


def publication_error(code: str, detail: str, status: int = 409) -> ApiHTTPException:
    return ApiHTTPException(status_code=status, error_code=code, detail=detail)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def snapshot_digest(snapshot: dict) -> str:
    return hashlib.sha256(canonical_bytes(snapshot)).hexdigest()


def reviewed_snapshot(
    db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID,
    filters: ArticleExportFilters, lock: bool = False,
) -> dict:
    context = build_export_query_context(user_id=actor.user.id, filters=filters, data_access=actor.access)
    ids = load_export_item_ids(db, context=context, limit=MAX_ARTICLES + 1)
    if len(ids) > MAX_ARTICLES:
        raise publication_error("publication_scope_too_large", "Narrow the filters to at most 100 articles for a reviewed publication.", 413)
    if lock and ids:
        # Team access is already fenced. Match the team's review lock order.
        db.execute(select(Item.id).where(Item.id.in_(ids)).order_by(Item.id).with_for_update(read=True)).all()
        db.execute(select(ItemIntelState.item_id).where(ItemIntelState.item_id.in_(ids)).order_by(ItemIntelState.item_id).with_for_update(read=True)).all()
    snapshot = snapshot_for_items(db, actor=actor, team_id=team_id, item_ids=ids)
    snapshot["matched_articles"] = len(ids)
    return snapshot


def snapshot_for_items(
    db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, item_ids: list[uuid.UUID],
) -> dict:
    now = datetime.now(timezone.utc)
    # Read only reviewed candidates. Neither article bodies nor AI summaries are
    # needed. Historical reviews retain every captured access label.
    rows = db.execute(select(
        IndicatorAssessment,
        IOC.id.label("ioc_id"), IOC.type,
        case((func.length(IOC.value_norm) <= 4096, IOC.value_norm), else_=None).label("value_norm"),
        func.substr(IndicatorAssessment.reason, 1, 2000).label("review_reason"),
        Item.id.label("item_id"), Item.feed_id, Item.classification_required_version,
        func.substr(Item.title, 1, 1000).label("title"),
        case((func.length(Item.url) <= 4096, Item.url), else_=None).label("url"),
        Feed.handling_label_id.label("feed_label"),
        ItemIntelState.revision.label("extraction_revision"), ItemIntelState.source_fingerprint,
        ItemIntelState.handling_label_id.label("state_label"),
        Article.id.label("article_id"), Article.retrieved_at, Article.content_purged_at,
        case((func.pg_column_size(ItemIOC.evidence_json) <= 32768, ItemIOC.evidence_json), else_=None).label("evidence"),
        ItemIOC.confidence, IOC.first_seen_at,
    ).join(IOC, IOC.id == IndicatorAssessment.ioc_id)
      .join(Item, Item.id == IndicatorAssessment.item_id)
      .join(Feed, Feed.id == Item.feed_id)
      .join(ItemIOC, (ItemIOC.item_id == Item.id) & (ItemIOC.ioc_id == IOC.id))
      .join(ItemIntelState, ItemIntelState.item_id == Item.id)
      .outerjoin(Article, Article.item_id == Item.id)
      .where(
          Item.id.in_(item_ids), IndicatorAssessment.team_id == team_id,
          IndicatorAssessment.verdict == "malicious",
          (IndicatorAssessment.expires_at.is_(None) | (IndicatorAssessment.expires_at > now)),
          IndicatorAssessment.source_revision == Item.classification_required_version,
          IndicatorAssessment.extraction_revision == ItemIntelState.revision,
          handling_label_access_predicate(Feed.handling_label_id, actor.access),
          handling_label_access_predicate(ItemIntelState.handling_label_id, actor.access),
          assessment_access_predicate(actor.access), IOC.type.in_(NETWORK_TYPES),
      ).options(defer(IndicatorAssessment.reason)).order_by(Item.id, IOC.id).limit(MAX_INDICATORS + 1)).all()
    if len(rows) > MAX_INDICATORS:
        raise publication_error("publication_scope_too_large", "Narrow the filters to at most 250 reviewed indicators.", 413)
    keys = [(row.type, row.value_norm) for row in rows]
    suppressed = set(db.execute(select(IndicatorSuppression.ioc_type, IndicatorSuppression.value_norm).where(
        IndicatorSuppression.team_id == team_id, IndicatorSuppression.active.is_(True),
        tuple_(IndicatorSuppression.ioc_type, IndicatorSuppression.value_norm).in_(keys),
        IndicatorSuppression.expires_at.is_(None) | (IndicatorSuppression.expires_at > now),
    )).all()) if keys else set()
    labels = assessment_labels_by_id(db, [row.IndicatorAssessment.id for row in rows])
    entries = []
    for row in rows:
        review = row.IndicatorAssessment
        if row.value_norm is None:
            raise publication_error("publication_indicator_too_large", "A reviewed indicator exceeds the supported value size.", 413)
        article = SimpleNamespace(id=row.article_id, retrieved_at=row.retrieved_at, content_purged_at=row.content_purged_at) if row.article_id else None
        if row.source_fingerprint != source_fingerprint(row, article):
            continue
        if (row.type, row.value_norm) in suppressed or indicator_exclusion(row.type, row.value_norm, None):
            continue
        if not row.evidence:
            # Never publish an unsupported or oversized observation as approved.
            raise publication_error("publication_evidence_unavailable", "A reviewed indicator has unavailable or oversized supporting evidence. Refresh its extraction before publishing.")
        boundary = labels.get(review.id, set()) | {row.feed_label, row.state_label}
        if None in boundary or review.handling_label_id not in boundary:
            raise publication_error("publication_lineage_unavailable", "A reviewed indicator has incomplete access history.")
        entries.append({
            "assessment_id": str(review.id), "assessment_version": review.version,
            "item_id": str(row.item_id), "feed_id": str(row.feed_id), "ioc_id": str(row.ioc_id),
            "type": row.type, "value": row.value_norm, "title": row.title, "url": row.url,
            "verdict": "malicious", "reason": row.review_reason,
            "source_revision": row.classification_required_version,
            "extraction_revision": row.extraction_revision,
            "source_fingerprint": row.source_fingerprint,
            "reviewed_at": review.updated_at.isoformat(),
            "expires_at": review.expires_at.isoformat() if review.expires_at else None,
            "evidence": row.evidence, "extraction_confidence": row.confidence,
            "first_seen_at": row.first_seen_at.isoformat(),
            "label_ids": sorted(str(label) for label in boundary),
        })
    total = db.scalar(select(func.count()).select_from(ItemIOC).where(ItemIOC.item_id.in_(item_ids))) or 0
    snapshot = {"schema_version": 1, "team_id": str(team_id), "indicators": entries,
                "excluded_or_unreviewed": max(0, total - len(entries))}
    if len(canonical_bytes(snapshot)) > MAX_SNAPSHOT_BYTES:
        raise publication_error("publication_evidence_too_large", "Reviewed evidence exceeds 1 MiB. Narrow the selected articles.", 413)
    return snapshot
