"""Bounded SQL projections of incomplete processing and durable dispatch state."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.article_recovery import article_repair_predicate
from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import ProcessingWork
from app.schemas.processing import (
    ProcessingStage,
    ProcessingWorkList,
    ProcessingWorkResponse,
)
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)

STAGES: tuple[ProcessingStage, ...] = ("article", "classification", "ioc", "tagging")
REASONS = {
    "worker_failed": "Processing failed. Retry the selected item after checking worker health.",
    "worker_interrupted": "The worker stopped before finishing. Recovery will retry within its allowance.",
    "source_changed": "The article changed after this work was selected. Refresh and retry its current revision.",
    "authorization_changed": "The accepting credential or source access changed. Start a new recovery with current access.",
    "cancelled": "Remaining recovery work was cancelled.",
    "item_deleted": "The selected article no longer exists.",
    "content_purged": "Article content was removed by lifecycle policy and will not be fetched by recovery.",
    "retry_exhausted": "Automatic attempts are exhausted. Inspect the failure before retrying.",
    "article_failed": "The source could not be fetched or extracted.",
    "tagging_incomplete": "One or more tagging rules could not be evaluated completely.",
    "busy": "Another worker currently owns this item. Recovery will retry later.",
}


class ProcessingConflict(ValueError):
    pass


class ProcessingCapacity(ValueError):
    pass


def stage_statement(stage: ProcessingStage):
    missing = {
        "article": and_(
            Article.content_purged_at.is_(None),
            or_(Article.id.is_(None), Article.text.is_(None), Item.status == "new"),
        ),
        "classification": or_(
            Item.classification_completed_version
            < Item.classification_required_version,
            ItemClassification.item_id.is_(None),
        ),
        "ioc": or_(
            Item.ioc_extraction_state.is_(None),
            and_(
                Item.ioc_extraction_state == "completed",
                ~exists().where(ItemIOC.item_id == Item.id),
            ),
        ),
        "tagging": Item.tagging_pending.is_(True),
    }[stage]
    same_source = ProcessingWork.source_version == Item.classification_required_version
    work_active = ProcessingWork.status.in_(("waiting", "queued", "running"))
    work_incomplete = and_(
        same_source, ProcessingWork.status.in_(("retry_wait", "attention"))
    )
    fallback_state = (
        case((Item.tagging_retry_at.is_(None), "attention"), else_="retry_wait")
        if stage == "tagging"
        else literal("pending")
    )
    state = case(
        (ProcessingWork.status == "waiting", "queued"),
        (or_(work_active, work_incomplete), ProcessingWork.status),
        else_=fallback_state,
    )
    statement = (
        select(
            Item.id.label("item_id"),
            literal(stage).label("stage"),
            func.substr(Item.title, 1, 500).label("title"),
            Feed.id.label("feed_id"),
            func.substr(Feed.name, 1, 255).label("feed_name"),
            Feed.handling_label_id.label("label_id"),
            Item.first_seen_at,
            Item.updated_at.label("source_updated_at"),
            (
                func.coalesce(
                    Item.tagging_pending_since_at, Item.classification_required_at
                )
                if stage == "tagging"
                else Item.classification_required_at
            ).label("required_since_at"),
            Item.classification_required_version.label("source_version"),
            ProcessingWork.id.label("work_id"),
            ProcessingWork.version.label("work_version"),
            ProcessingWork.generation,
            ProcessingWork.status.label("work_status"),
            ProcessingWork.source_version.label("work_source_version"),
            state.label("state"),
            case(
                (or_(work_active, work_incomplete), ProcessingWork.reason), else_=None
            ).label("reason"),
            case(
                (or_(work_active, work_incomplete), ProcessingWork.attempts),
                else_=Item.tagging_attempts if stage == "tagging" else 0,
            ).label("attempts"),
            case(
                (or_(work_active, work_incomplete), ProcessingWork.next_retry_at),
                else_=Item.tagging_retry_at if stage == "tagging" else None,
            ).label("next_retry_at"),
            (
                article_repair_predicate(
                    dispatch_after_seconds=get_settings().dispatch_items_missing_articles_after_seconds
                )
                if stage == "article"
                else literal(False)
            ).label("article_repair_eligible"),
            Article.error.label("article_error"),
            Article.retrieved_at.label("article_retrieved_at"),
        )
        .select_from(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .outerjoin(
            ProcessingWork,
            and_(ProcessingWork.item_id == Item.id, ProcessingWork.stage == stage),
        )
        .where(or_(missing, work_active, work_incomplete))
    )
    if stage == "article":
        statement = statement.where(
            or_(Article.content_purged_at.is_(None), work_active, work_incomplete)
        )
    return statement


def work_query(*, stage: ProcessingStage | None = None):
    return union_all(
        *(stage_statement(value) for value in ((stage,) if stage else STAGES))
    ).subquery()


def work_revision(row) -> str:
    value = [
        str(row.item_id),
        row.stage,
        row.source_version,
        str(row.source_updated_at),
        str(row.feed_id),
        str(row.label_id),
        row.work_version,
        row.generation,
        row.state,
    ]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:32]


def encode_cursor(values: list[str]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(values, separators=(",", ":")).encode()
    ).decode()


def decode_cursor(value: str, *, length: int) -> list[str]:
    try:
        if len(value) > 2048:
            raise ValueError
        result = json.loads(base64.urlsafe_b64decode(value.encode()))
        if (
            not isinstance(result, list)
            or len(result) != length
            or not all(isinstance(entry, str) for entry in result)
        ):
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProcessingConflict(
            "The result cursor is invalid. Reload the worklist."
        ) from exc


def list_processing_work(
    db: Session,
    access: DataAccessContext,
    *,
    can_retry: bool,
    stage: ProcessingStage | None = None,
    state: str | None = None,
    feed_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> ProcessingWorkList:
    rows = work_query(stage=stage)
    query = select(rows).where(handling_label_access_predicate(rows.c.label_id, access))
    fingerprint = f"{stage or ''}:{state or ''}:{feed_id or ''}"
    if state:
        query = query.where(rows.c.state == state)
    if feed_id:
        query = query.where(rows.c.feed_id == feed_id)
    if cursor:
        scope, raw_time, raw_id, last_stage = decode_cursor(cursor, length=4)
        if scope != fingerprint:
            raise ProcessingConflict("The result cursor belongs to different filters.")
        try:
            at, identity = datetime.fromisoformat(raw_time), uuid.UUID(raw_id)
        except ValueError as exc:
            raise ProcessingConflict("The result cursor is invalid.") from exc
        query = query.where(
            or_(
                rows.c.first_seen_at > at,
                and_(rows.c.first_seen_at == at, rows.c.item_id > identity),
                and_(
                    rows.c.first_seen_at == at,
                    rows.c.item_id == identity,
                    rows.c.stage > last_stage,
                ),
            )
        )
    found = db.execute(
        query.order_by(rows.c.first_seen_at, rows.c.item_id, rows.c.stage).limit(
            limit + 1
        )
    ).all()
    now = datetime.now(timezone.utc)
    items = [
        ProcessingWorkResponse(
            item_id=row.item_id,
            stage=row.stage,
            revision=work_revision(row),
            title=row.title,
            feed_id=row.feed_id,
            feed_name=row.feed_name,
            state=row.state,
            reason=row.reason,
            message=REASONS.get(row.reason),
            first_seen_at=row.first_seen_at,
            age_seconds=max(0, int((now - row.required_since_at).total_seconds())),
            attempts=row.attempts,
            next_retry_at=row.next_retry_at,
            can_retry=can_retry
            and row.state not in {"queued", "running"}
            and row.reason != "content_purged",
        )
        for row in found[:limit]
    ]
    last = found[limit - 1] if len(found) > limit else None
    return ProcessingWorkList(
        items=items,
        has_more=last is not None,
        next_cursor=encode_cursor(
            [fingerprint, last.first_seen_at.isoformat(), str(last.item_id), last.stage]
        )
        if last
        else None,
    )


def selected_work(db: Session, item_id: uuid.UUID, stage: ProcessingStage):
    return db.execute(stage_statement(stage).where(Item.id == item_id)).one_or_none()
