"""Team-approved publications with exact previews and current read authorization."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models.feed import Feed
from app.models.indicator_publication import IndicatorPublication, IndicatorPublicationLabel, IndicatorPublicationSource
from app.models.item import Item
from app.schemas.exports import ArticleExportFilters
from app.schemas.indicator_publications import PublicationCreate, PublicationPage, PublicationPreview, PublicationResponse, ReviewedIndicatorPreview
from app.services.audit import record_audit
from app.services.data_access_policy import handling_label_access_predicate
from app.services.indicator_assessments import fence_indicator_request
from app.services.indicator_publication_query import publication_error, reviewed_snapshot, snapshot_digest
from app.services.indicator_publication_refresh import refresh_publication
from app.services.team_assessment_access import AssessmentRequest


def publication_access(actor: AssessmentRequest):
    label = IndicatorPublicationLabel
    source = IndicatorPublicationSource
    labels = select(label.publication_id).where(label.publication_id == IndicatorPublication.id).correlate(IndicatorPublication)
    sources = select(source.publication_id).where(source.publication_id == IndicatorPublication.id).correlate(IndicatorPublication)
    unavailable = sources.outerjoin(Item, Item.id == source.item_id).outerjoin(Feed, Feed.id == Item.feed_id).where(or_(
        Item.id.is_(None), Feed.id.is_(None), Feed.id != source.feed_id,
        ~handling_label_access_predicate(Feed.handling_label_id, actor.access),
    ))
    return and_(labels.exists(), sources.exists(), ~unavailable.exists(), ~labels.where(
        ~handling_label_access_predicate(label.handling_label_id, actor.access),
    ).exists())


def preview_publication(db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, filters: ArticleExportFilters) -> PublicationPreview:
    fence_indicator_request(db, actor, team_id=team_id)
    snapshot = reviewed_snapshot(db, actor=actor, team_id=team_id, filters=filters)
    result = PublicationPreview(
        fingerprint=snapshot_digest(snapshot), matched_articles=snapshot["matched_articles"],
        excluded_or_unreviewed=snapshot["excluded_or_unreviewed"],
        indicators=[ReviewedIndicatorPreview(**{key: entry[key] for key in (
            "assessment_id", "item_id", "ioc_id", "type", "value", "title", "assessment_version",
            "source_revision", "extraction_revision", "expires_at",
        )}, evidence_count=len(entry["evidence"])) for entry in snapshot["indicators"]],
    )
    fence_indicator_request(db, actor, team_id=team_id)
    return result


def create_publication(db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, payload: PublicationCreate) -> IndicatorPublication:
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    digest = snapshot_digest(payload.model_dump(mode="json"))
    existing = db.scalar(select(IndicatorPublication).where(
        IndicatorPublication.team_id == team_id, IndicatorPublication.idempotency_key == payload.idempotency_key,
    ))
    if existing is not None:
        if existing.created_by_user_id != actor.user.id or existing.request_digest != digest:
            raise publication_error("publication_idempotency_conflict", "This publication request identity was already used for different content.")
        return load_publication(db, actor=actor, team_id=team_id, publication_id=existing.id)
    count = db.scalar(select(func.count()).select_from(IndicatorPublication).where(IndicatorPublication.team_id == team_id)) or 0
    if count >= 1000:
        raise publication_error("publication_capacity", "This team retains 1,000 reviewed publications. Withdraw unused publications; withdrawn history expires after 180 days.", 429)
    snapshot = reviewed_snapshot(db, actor=actor, team_id=team_id, filters=payload.filters, lock=True)
    if snapshot_digest(snapshot) != payload.preview_fingerprint:
        raise publication_error("publication_preview_changed", "Evidence, verdicts or the selected articles changed. Refresh and review the preview before publishing.")
    if not snapshot["indicators"]:
        raise publication_error("publication_empty", "No current, supported and unsuppressed malicious indicators were selected.")
    now = datetime.now(timezone.utc)
    snapshot["misp_timestamp"] = int(now.timestamp())
    row = IndicatorPublication(
        id=uuid.uuid4(), team_id=team_id, created_by_user_id=actor.user.id,
        idempotency_key=payload.idempotency_key, request_digest=digest,
        format=payload.format, marking=payload.marking, distribution=payload.misp_distribution,
        snapshot_json=snapshot, status="active", revision=1,
        indicator_count=len(snapshot["indicators"]), withdrawn_count=0,
        created_at=now, updated_at=now, next_check_at=now,
    )
    db.add(row)
    db.flush()
    labels = {uuid.UUID(label) for entry in snapshot["indicators"] for label in entry["label_ids"]}
    sources = {(uuid.UUID(entry["item_id"]), uuid.UUID(entry["feed_id"])) for entry in snapshot["indicators"]}
    db.add_all(IndicatorPublicationLabel(publication_id=row.id, handling_label_id=label) for label in labels)
    db.add_all(IndicatorPublicationSource(publication_id=row.id, item_id=item, feed_id=feed) for item, feed in sources)
    record_audit(db, actor_user_id=actor.user.id, action="intelligence.publication.create",
                 resource_type="indicator_publication", resource_id=str(row.id),
                 data_access_governed=True, data_access_label_ids=labels,
                 metadata={"team_id": str(team_id), "format": payload.format, "indicator_count": row.indicator_count})
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    return row


def withdraw_publication(db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID,
                         publication_id: uuid.UUID, expected_revision: int) -> IndicatorPublication:
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    row = load_publication(db, actor=actor, team_id=team_id, publication_id=publication_id)
    if row.status == "withdrawn" and row.revision in {expected_revision, expected_revision + 1}:
        return row
    if row.revision != expected_revision:
        raise publication_error("publication_revision_changed", "Publication status changed. Refresh it before withdrawing.")
    now = datetime.now(timezone.utc)
    snapshot = deepcopy(row.snapshot_json)
    for entry in snapshot["indicators"]:
        if not entry.get("withdrawn_at"):
            entry.update(withdrawn_at=now.isoformat(), withdrawal_reason="operator_withdrawal")
    snapshot["misp_timestamp"] = max(int(now.timestamp()), int(snapshot.get("misp_timestamp", row.created_at.timestamp())) + 1)
    row.snapshot_json = snapshot
    row.withdrawn_count = row.indicator_count
    row.status = "withdrawn"
    row.revision += 1
    row.updated_at = now
    record_audit(db, actor_user_id=actor.user.id, action="intelligence.publication.withdraw",
                 resource_type="indicator_publication", resource_id=str(row.id),
                 data_access_governed=True,
                 data_access_label_ids={label for entry in snapshot["indicators"] for label in entry["label_ids"]},
                 metadata={"team_id": str(team_id), "revision": row.revision})
    fence_indicator_request(db, actor, team_id=team_id, write=True)
    return row


def load_publication(db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, publication_id: uuid.UUID) -> IndicatorPublication:
    fence_indicator_request(db, actor, team_id=team_id)
    source_ids = list(db.scalars(select(IndicatorPublicationSource.item_id).join(
        IndicatorPublication, IndicatorPublication.id == IndicatorPublicationSource.publication_id,
    ).where(IndicatorPublication.id == publication_id, IndicatorPublication.team_id == team_id, publication_access(actor))))
    if source_ids:
        from app.models.intel_assessment import ItemIntelState

        db.execute(select(Item.id).where(Item.id.in_(source_ids)).order_by(Item.id).with_for_update(read=True)).all()
        db.execute(select(ItemIntelState.item_id).where(ItemIntelState.item_id.in_(source_ids)).order_by(ItemIntelState.item_id).with_for_update(read=True)).all()
    row = db.scalar(select(IndicatorPublication).where(
        IndicatorPublication.id == publication_id, IndicatorPublication.team_id == team_id, publication_access(actor),
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise publication_error("publication_not_found", "Publication not found or its retained evidence is no longer accessible.", 404)
    refresh_publication(db, row)
    fence_indicator_request(db, actor, team_id=team_id)
    return row


def list_publications(db: Session, *, actor: AssessmentRequest, team_id: uuid.UUID, cursor: str | None, limit: int) -> PublicationPage:
    fence_indicator_request(db, actor, team_id=team_id)
    query = select(IndicatorPublication).where(IndicatorPublication.team_id == team_id, publication_access(actor))
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode() + b"=" * (-len(cursor) % 4)))
            if value["team"] != str(team_id):
                raise ValueError("Wrong team")
            at, identifier = datetime.fromisoformat(value["at"]), uuid.UUID(value["id"])
            if at.tzinfo is None:
                raise ValueError("Naive timestamp")
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise publication_error("publication_cursor_invalid", "The publication cursor is invalid; start from the first page.", 400) from exc
        query = query.where(or_(IndicatorPublication.created_at < at, and_(IndicatorPublication.created_at == at, IndicatorPublication.id < identifier)))
    columns = [getattr(IndicatorPublication, name) for name in PublicationResponse.model_fields]
    rows = db.scalars(query.options(load_only(*columns)).order_by(
        IndicatorPublication.created_at.desc(), IndicatorPublication.id.desc(),
    ).limit(limit + 1)).all()
    more, selected = len(rows) > limit, rows[:limit]
    token = None
    if more:
        last = selected[-1]
        token = base64.urlsafe_b64encode(json.dumps({"at": last.created_at.isoformat(), "id": str(last.id), "team": str(team_id)}).encode()).decode().rstrip("=")
    fence_indicator_request(db, actor, team_id=team_id)
    return PublicationPage(items=[PublicationResponse.model_validate(row) for row in selected], has_more=more, next_cursor=token)
