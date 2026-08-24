from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, exists, func, or_, select

from app.models.ai_task_run import AITaskRun
from app.models.article import Article
from app.models.feed import Feed
from app.models.ioc import ItemIOC
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.services.ai_ops import AI_STATUS_QUEUED, AI_STATUS_RUNNING, AI_TASK_TYPE_ITEM_ENRICHMENT


def dispatch_due_feeds(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    claim_feed_for_dispatch: Callable[..., bool],
    fetch_feed_task: Any,
    clear_feed_dispatch_claim: Callable[[Feed], None],
    next_feed_fetch_at: Callable[[Feed, datetime], datetime | None],
    logger: logging.Logger,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    queued = 0

    with db_session_factory() as db:
        batch_size = max(0, int(settings.dispatch_due_feeds_batch_size))
        if batch_size <= 0:
            return {"queued": 0}
        feed_ids = db.scalars(
            select(Feed.id)
            .where(
                Feed.enabled.is_(True),
                or_(Feed.next_fetch_at.is_(None), Feed.next_fetch_at <= now),
                or_(Feed.dispatch_backoff_until.is_(None), Feed.dispatch_backoff_until <= now),
            )
            .order_by(Feed.next_fetch_at.asc(), Feed.created_at.asc())
            .limit(batch_size * 5)
        ).all()
        for feed_id in feed_ids:
            if queued >= batch_size:
                break
            if not claim_feed_for_dispatch(db, feed_id=feed_id, now=now):
                db.rollback()
                continue
            db.commit()
            try:
                fetch_feed_task.delay(str(feed_id))
            except Exception as exc:
                logger.exception("feed_dispatch_enqueue_failed feed_id=%s error=%s", feed_id, exc)
                claimed_feed = db.scalar(select(Feed).where(Feed.id == feed_id))
                if claimed_feed is not None:
                    clear_feed_dispatch_claim(claimed_feed)
                    claimed_feed.next_fetch_at = next_feed_fetch_at(claimed_feed, datetime.now(timezone.utc))
                    db.add(claimed_feed)
                db.commit()
                continue
            queued += 1

    return {"queued": queued}


def dispatch_unclassified_items(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    enqueue_classification_task: Callable[[str], bool],
) -> dict[str, int]:
    queued = 0
    with db_session_factory() as db:
        item_ids = db.scalars(
            select(Item.id)
            .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
            .where(ItemClassification.item_id.is_(None))
            .order_by(Item.first_seen_at.asc())
            .limit(settings.dispatch_unclassified_items_batch_size)
        ).all()

    for item_id in item_ids:
        if enqueue_classification_task(str(item_id)):
            queued += 1

    return {"queued": queued}


def dispatch_items_missing_articles(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    list_item_ids_missing_articles: Callable[..., list[uuid.UUID]],
    fetch_article_task: Any,
    logger: logging.Logger,
) -> dict[str, int]:
    with db_session_factory() as db:
        item_ids = list_item_ids_missing_articles(
            db,
            limit=settings.dispatch_items_missing_articles_batch_size,
        )

    queued = 0
    for item_id in item_ids:
        try:
            fetch_article_task.delay(str(item_id))
        except Exception as exc:
            logger.exception("article_fetch_repair_enqueue_failed item_id=%s error=%s", item_id, exc)
            continue
        queued += 1

    return {"queued": queued}


def dispatch_items_missing_iocs(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    completed_state: str,
    extract_item_iocs_task: Any,
    logger: logging.Logger,
) -> dict[str, int]:
    queued = 0
    with db_session_factory() as db:
        missing_ioc_links = ~exists(select(ItemIOC.item_id).where(ItemIOC.item_id == Item.id))
        item_ids = db.scalars(
            select(Item.id)
            .where(
                or_(
                    Item.ioc_extraction_state.is_(None),
                    and_(
                        Item.ioc_extraction_state == completed_state,
                        missing_ioc_links,
                    ),
                )
            )
            .order_by(Item.first_seen_at.asc())
            .limit(settings.dispatch_items_missing_iocs_batch_size)
        ).all()

    for item_id in item_ids:
        try:
            extract_item_iocs_task.delay(str(item_id))
        except Exception as exc:
            logger.exception("item_ioc_repair_enqueue_failed item_id=%s error=%s", item_id, exc)
            continue
        queued += 1

    return {"queued": queued}


def dispatch_items_missing_ai_enrichment(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    load_active_ai_settings: Callable[..., Any],
    reconcile_stale_ai_runs: Callable[..., Any],
    auto_enrich_cutoff: Callable[[datetime], datetime],
    auto_enrich_window_hours: Callable[[], int],
    safe_queue_item_ai_enrichment_run: Callable[..., bool],
    trigger_source: str,
) -> dict[str, int | str]:
    queued = 0
    with db_session_factory() as db:
        active = load_active_ai_settings(db)
        if not active.ai_enabled:
            return {"queued": 0, "reason": "ai_disabled"}
        if not active.ai_configured:
            return {"queued": 0, "reason": "ai_not_configured"}
        if not active.auto_enrich_new_items:
            return {"queued": 0, "reason": "auto_enrich_disabled"}
        reconcile_stale_ai_runs(db)
        now = datetime.now(timezone.utc)
        cutoff = auto_enrich_cutoff(now)
        window_hours = auto_enrich_window_hours()
        error_recovery_cutoff = now - timedelta(
            seconds=max(0, int(settings.dispatch_items_failed_ai_enrichment_after_seconds))
        )

        in_flight_enrichment_run = exists(
            select(AITaskRun.id).where(
                AITaskRun.item_id == Item.id,
                AITaskRun.task_type == AI_TASK_TYPE_ITEM_ENRICHMENT,
                AITaskRun.status.in_([AI_STATUS_QUEUED, AI_STATUS_RUNNING]),
            )
        )
        item_ids = db.scalars(
            select(Item.id)
            .join(ItemClassification, ItemClassification.item_id == Item.id)
            .join(Article, Article.item_id == Item.id)
            .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
            .where(
                Item.status == "content_fetched",
                Item.published_at.is_not(None),
                Item.published_at >= cutoff,
                Item.first_seen_at >= cutoff,
                ItemClassification.classified_at >= cutoff,
                Article.text.is_not(None),
                Article.text != "",
                or_(
                    ItemAIEnrichment.item_id.is_(None),
                    and_(
                        ItemAIEnrichment.status == "error",
                        ItemAIEnrichment.updated_at <= error_recovery_cutoff,
                    ),
                ),
                ~in_flight_enrichment_run,
            )
            .order_by(ItemClassification.classified_at.asc(), Item.first_seen_at.asc())
            .limit(settings.dispatch_items_missing_ai_enrichment_batch_size)
        ).all()

    for item_id in item_ids:
        if safe_queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=trigger_source,
            reason=None,
            model=getattr(active, "model", None),
            metadata={
                "recovery": "recent_missing_or_failed_enrichment",
                "force": False,
                "auto_enrich_new_item_max_age_hours": window_hours,
            },
        ):
            queued += 1

    return {"queued": queued}


def dispatch_feed_metadata_backfill(
    *,
    db_session_factory: Callable[..., Any],
    settings: Any,
    needs_feed_metadata_backfill: Callable[[Feed], bool],
    backfill_feed_metadata_task: Any,
    logger: logging.Logger,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    queued = 0
    with db_session_factory() as db:
        feeds = db.scalars(
            select(Feed)
            .where(
                Feed.enabled.is_(True),
                or_(
                    Feed.dispatch_backoff_until.is_(None),
                    Feed.dispatch_backoff_until <= now,
                ),
                or_(
                    func.trim(Feed.name) == "",
                    func.lower(func.trim(Feed.name)).like("http://%"),
                    func.lower(func.trim(Feed.name)).like("https://%"),
                    Feed.site_url.is_(None),
                ),
            )
            .order_by(Feed.created_at.asc())
            .limit(settings.dispatch_feed_metadata_scan_limit)
        ).all()

    for feed in feeds:
        if queued >= settings.dispatch_feed_metadata_queue_limit:
            break
        if not needs_feed_metadata_backfill(feed):
            continue
        try:
            backfill_feed_metadata_task.delay(str(feed.id))
        except Exception as exc:
            logger.exception("feed_metadata_backfill_enqueue_failed feed_id=%s error=%s", feed.id, exc)
            continue
        queued += 1

    return {"queued": queued}
