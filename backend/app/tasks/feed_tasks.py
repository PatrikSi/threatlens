import logging
import uuid
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy.orm import Session

from app.core import config
from app.core.redis_client import redis_client_from_url
from app.models.item import Item
from app.services import ai_config, ai_ops, feed_metadata, feed_pipeline
from app.services.ai_ops import (
    AI_TASK_TYPE_ITEM_ENRICHMENT,
    _reconcile_stale_ai_runs,
    get_ai_task_run_stop_reason,
    queue_ai_task_run,
    update_ai_task_run_celery,
)
from app.services.feed_metadata import (
    backfill_feed_metadata_from_body as _backfill_feed_metadata_from_body,
)
from app.services.feed_metadata import (
    needs_feed_metadata_backfill as _needs_feed_metadata_backfill,
)
from app.services.feed_pipeline import (
    claim_feed_for_dispatch as _claim_feed_for_dispatch_impl,
)
from app.services.feed_pipeline import (
    list_item_ids_missing_articles as _list_item_ids_missing_articles_impl,
)
from app.tasks import feed_task_runtime, feed_task_scheduling
from app.tasks.ai_brief_tasks import (
    DAILY_BRIEF_STALE_RETRY_WINDOW,
    _daily_brief_backfill_attempt_is_settled,
    _daily_brief_backfill_attempt_number,
    _daily_brief_backfill_attempts,
    _daily_brief_backfill_reference_times,
    _is_stale_daily_brief_task_run,
    _scheduled_daily_ai_brief_due,
    backfill_daily_ai_briefs,
    dispatch_daily_ai_brief_generation,
    reconcile_ai_task_runs,
)
from app.tasks.article_fetch_tasks import run_fetch_article as _run_fetch_article
from app.tasks.celery_app import celery_app
from app.tasks.feed_fetch_tasks import (
    run_backfill_feed_metadata as _run_backfill_feed_metadata,
)
from app.tasks.feed_fetch_tasks import (
    run_fetch_feed as _run_fetch_feed,
)
from app.tasks.feed_task_constants import (
    AI_AUTO_ENRICH_OUTSIDE_NEW_ITEM_WINDOW_REASON,
    ARTICLE_REFRESHED_SKIP_REASON,
    IOC_EXTRACTION_STATE_COMPLETED,
    IOC_EXTRACTION_STATE_COMPLETED_EMPTY,
    TAGGING_REAPPLY_COMMIT_INTERVAL,
)
from app.tasks.feed_task_coordination import (
    DOMAIN_SLOT_TTL_SECONDS,
    DOMAIN_SLOT_WAIT_INTERVAL_SECONDS,
    TAGGING_REAPPLY_LOCK_KEY,
    CoordinationUnavailableError,
    _best_effort_release_lease,
    _domain_slot_key,
    _lease_heartbeat_is_stale,
    _lease_heartbeat_key,
    _lease_heartbeat_value,
    _lease_remaining_ttl_ms,
    _lease_renewal_interval_seconds,
    _lease_takeover_stale_after_seconds,
    _parse_lease_heartbeat,
    _redis_lease_heartbeat,
    _try_take_stale_lease,
    _write_lease_heartbeat,
    claim_tagging_reapply_dispatch,
    daily_ai_brief_lock,
    release_tagging_reapply_dispatch,
)
from app.tasks.feed_task_coordination import domain_slot as domain_slot
from app.tasks.feed_task_coordination import feed_lock as feed_lock
from app.tasks.feed_task_dependencies import (
    ArticleFetchDependencies,
    ArticleFetchOptions,
    FeedFetchDependencies,
    FeedFetchOptions,
    ItemAIDependencies,
    ItemProcessingDependencies,
)
from app.tasks.feed_task_dispatchers import (
    dispatch_due_feeds as _dispatch_due_feeds,
)
from app.tasks.feed_task_dispatchers import (
    dispatch_feed_metadata_backfill as _dispatch_feed_metadata_backfill,
)
from app.tasks.feed_task_dispatchers import (
    dispatch_items_missing_ai_enrichment as _dispatch_items_missing_ai_enrichment,
)
from app.tasks.feed_task_events import (
    emit_item_integration_event as _emit_item_integration_event,
)
from app.tasks.feed_task_runtime import (
    article_freshness_token as _article_freshness_token,
)
from app.tasks.feed_task_runtime import (
    feed_url_digest_still_current as _feed_url_digest_still_current,
)
from app.tasks.feed_task_runtime import (
    task_run_claimed_by_current_worker as _task_run_claimed_by_current_worker,
)
from app.tasks.feed_task_scheduling import is_feed_due as _is_feed_due
from app.tasks.feed_task_scheduling import (
    is_scheduled_feed_due as _is_scheduled_feed_due,
)
from app.tasks.feed_task_scheduling import (
    next_feed_fetch_at as _next_feed_fetch_at,
)
from app.tasks.feed_task_scheduling import (
    next_scheduled_feed_fetch_at as _next_scheduled_feed_fetch_at,
)
from app.tasks.feed_task_scheduling import (
    reschedule_feed_after_coordination_failure as _reschedule_feed_after_coordination_failure,
)
from app.tasks.feed_task_scheduling import (
    stage_feed_after_coordination_failure as _stage_feed_after_coordination_failure,
)
from app.tasks.feed_task_storage import (
    RSS_SUMMARY_FALLBACK_EXTRACTION_METHOD,
)
from app.tasks.feed_task_storage import (
    rss_summary_fallback_text as _rss_summary_fallback_text,
)
from app.tasks.integration_tasks import (
    dispatch_pending_integration_deliveries,
    dispatch_pending_integration_events,
    enqueue_integration_delivery_processing,
    maintain_integration_delivery_history,
    process_integration_deliveries,
    route_integration_event,
)
from app.tasks.item_ai_tasks import (
    parse_datetime_text as _parse_datetime_text_impl,
)
from app.tasks.item_ai_tasks import (
    parse_uuid_text_list as _parse_uuid_text_list_impl,
)
from app.tasks.item_ai_tasks import (
    run_generate_item_ai_enrichment as _run_generate_item_ai_enrichment,
)
from app.tasks.item_ai_tasks import (
    run_reprocess_recent_ai_items as _run_reprocess_recent_ai_items,
)
from app.tasks.item_processing_tasks import (
    run_classify_item as _run_classify_item,
)
from app.tasks.item_processing_tasks import (
    run_extract_item_iocs as _run_extract_item_iocs,
)
from app.tasks.item_processing_tasks import (
    run_reapply_recent_item_tags as _run_reapply_recent_item_tags,
)
from app.tasks.notification_tasks import (
    _emit_failed_webhook_integration_event,
    _enqueue_smtp_alert_match_notification,
    _enqueue_smtp_feed_failing_notification,
    _enqueue_smtp_new_item_notifications,
    _feed_failing_smtp_scope_key,
    _mark_failed_webhook_delivery_dead_letter,
    _process_reserved_notification_deliveries,
    dispatch_alert_match_notification_webhooks,
    dispatch_daily_digest_notification_webhooks,
    dispatch_feed_failing_notification_webhooks,
    dispatch_new_item_notification_webhooks,
    dispatch_pending_notification_webhook_deliveries,
    dispatch_smtp_alert_match_notification,
    dispatch_smtp_feed_failing_notification,
    dispatch_smtp_new_item_notification,
    dispatch_smtp_webhook_failed_notification,
    dispatch_webhook_failed_notification_webhooks,
    enqueue_notification_webhook_delivery_processing,
    process_notification_webhook_deliveries,
    reserve_notification_webhook_delivery,
)
from app.tasks.notification_tasks import (
    mark_feed_failure_and_enqueue_notifications as _mark_feed_failure_and_enqueue_notifications,
)
from app.tasks.report_tasks import (
    dispatch_due_report_schedules,
    dispatch_pending_report_tasks,
    generate_intelligence_report,
)
from app.tasks.task_session import db_session

settings = config.get_settings()
redis_client = redis_client_from_url(
    settings.redis_url, decode_responses=True, settings=settings
)
logger = logging.getLogger(__name__)

__all__ = [
    "_mark_feed_failure_and_enqueue_notifications",
    "feed_lock",
    "domain_slot",
    "_is_feed_due",
    "_feed_url_digest_still_current",
    "_backfill_feed_metadata_from_body",
    "AI_AUTO_ENRICH_OUTSIDE_NEW_ITEM_WINDOW_REASON",
    "TAGGING_REAPPLY_COMMIT_INTERVAL",
    "ARTICLE_REFRESHED_SKIP_REASON",
    "IOC_EXTRACTION_STATE_COMPLETED_EMPTY",
    "IOC_EXTRACTION_STATE_COMPLETED",
    "_task_run_claimed_by_current_worker",
    "_needs_feed_metadata_backfill",
    "_emit_item_integration_event",
    "_reschedule_feed_after_coordination_failure",
    "_stage_feed_after_coordination_failure",
    "CoordinationUnavailableError",
    "DAILY_BRIEF_STALE_RETRY_WINDOW",
    "DOMAIN_SLOT_TTL_SECONDS",
    "DOMAIN_SLOT_WAIT_INTERVAL_SECONDS",
    "RSS_SUMMARY_FALLBACK_EXTRACTION_METHOD",
    "TAGGING_REAPPLY_LOCK_KEY",
    "_best_effort_release_lease",
    "_article_freshness_token",
    "_daily_brief_backfill_attempt_is_settled",
    "_daily_brief_backfill_attempt_number",
    "_daily_brief_backfill_attempts",
    "_daily_brief_backfill_reference_times",
    "_domain_slot_key",
    "_emit_failed_webhook_integration_event",
    "_enqueue_smtp_alert_match_notification",
    "_enqueue_smtp_feed_failing_notification",
    "_enqueue_smtp_new_item_notifications",
    "_feed_failing_smtp_scope_key",
    "_is_stale_daily_brief_task_run",
    "_is_scheduled_feed_due",
    "_lease_heartbeat_is_stale",
    "_lease_heartbeat_key",
    "_lease_heartbeat_value",
    "_lease_remaining_ttl_ms",
    "_lease_renewal_interval_seconds",
    "_lease_takeover_stale_after_seconds",
    "_mark_failed_webhook_delivery_dead_letter",
    "_next_scheduled_feed_fetch_at",
    "_parse_lease_heartbeat",
    "_process_reserved_notification_deliveries",
    "_redis_lease_heartbeat",
    "_rss_summary_fallback_text",
    "_scheduled_daily_ai_brief_due",
    "_try_take_stale_lease",
    "_write_lease_heartbeat",
    "backfill_daily_ai_briefs",
    "claim_tagging_reapply_dispatch",
    "daily_ai_brief_lock",
    "dispatch_daily_ai_brief_generation",
    "dispatch_due_report_schedules",
    "dispatch_pending_report_tasks",
    "dispatch_alert_match_notification_webhooks",
    "dispatch_daily_digest_notification_webhooks",
    "dispatch_feed_failing_notification_webhooks",
    "generate_intelligence_report",
    "dispatch_new_item_notification_webhooks",
    "dispatch_pending_notification_webhook_deliveries",
    "dispatch_pending_integration_deliveries",
    "dispatch_pending_integration_events",
    "dispatch_smtp_alert_match_notification",
    "dispatch_smtp_feed_failing_notification",
    "dispatch_smtp_new_item_notification",
    "dispatch_smtp_webhook_failed_notification",
    "dispatch_webhook_failed_notification_webhooks",
    "enqueue_integration_delivery_processing",
    "enqueue_notification_webhook_delivery_processing",
    "maintain_integration_delivery_history",
    "process_integration_deliveries",
    "process_notification_webhook_deliveries",
    "reconcile_ai_task_runs",
    "release_tagging_reapply_dispatch",
    "reserve_notification_webhook_delivery",
    "route_integration_event",
]


def _enqueue_classification_task(item_id: str) -> bool:
    try:
        classify_item.delay(item_id)
    except Exception as exc:
        logger.exception(
            "item_classification_enqueue_failed item_id=%s error=%s", item_id, exc
        )
        return False
    return True


def enqueue_article_fetch_processing(item_ids: list[uuid.UUID]) -> bool:
    if not item_ids:
        return True

    all_enqueued = True
    for item_id in item_ids:
        try:
            fetch_article.delay(str(item_id))
        except Exception as exc:
            all_enqueued = False
            logger.exception(
                "article_fetch_enqueue_failed item_id=%s error=%s", item_id, exc
            )
    return all_enqueued


def _claim_feed_for_dispatch(db: Session, *, feed_id: uuid.UUID, now: datetime) -> bool:
    return _claim_feed_for_dispatch_impl(
        db,
        feed_id=feed_id,
        now=now,
        claim_seconds=settings.dispatch_feed_claim_seconds,
        is_feed_due=feed_task_scheduling.is_feed_due,
        next_fetch_at=_next_feed_fetch_at,
    )


def _list_item_ids_missing_articles(
    db: Session, *, limit: int, now: datetime | None = None
) -> list[uuid.UUID]:
    return _list_item_ids_missing_articles_impl(
        db,
        limit=limit,
        now=now,
        dispatch_after_seconds=settings.dispatch_items_missing_articles_after_seconds,
    )


def _update_task_run_celery_id(run_id: uuid.UUID, celery_task_id: str | None) -> None:
    with db_session() as db:
        update_ai_task_run_celery(db, run_id=run_id, celery_task_id=celery_task_id)
        db.commit()


def _queue_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str | None,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    force: bool = False,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> uuid.UUID:
    with db_session() as db:
        run = queue_ai_task_run(
            db,
            task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
            trigger_source=trigger_source,
            actor_user_id=actor_user_id,
            item_id=item_id,
            parent_run_id=parent_run_id,
            model=model,
            metadata=metadata,
            reason=reason,
        )
        db.commit()
        run_id = run.id
    try:
        task = generate_item_ai_enrichment_task.delay(
            str(item_id), force=force, task_run_id=str(run_id)
        )
    except Exception:
        with db_session() as db:
            ai_ops.finish_ai_task_run(
                db,
                run_id=run_id,
                status=ai_ops.AI_STATUS_ERROR,
                reason="enqueue_failed",
                error="task_queue_unavailable",
                worker_name="api",
                metadata_updates={"force": bool(force)},
            )
            db.commit()
        raise
    task_id = getattr(task, "id", None)
    if task_id:
        _update_task_run_celery_id(run_id, task_id)
    return run_id


def _safe_enqueue_item_iocs(item_id: uuid.UUID) -> bool:
    try:
        extract_item_iocs.delay(str(item_id))
    except Exception as exc:
        logger.exception("item_ioc_enqueue_failed item_id=%s error=%s", item_id, exc)
        return False
    return True


def _safe_queue_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str | None,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    force: bool = False,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> bool:
    try:
        _queue_item_ai_enrichment_run(
            item_id=item_id,
            trigger_source=trigger_source,
            reason=reason,
            actor_user_id=actor_user_id,
            parent_run_id=parent_run_id,
            force=force,
            model=model,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception(
            "item_ai_enrichment_enqueue_failed item_id=%s parent_run_id=%s error=%s",
            item_id,
            parent_run_id,
            exc,
        )
        return False
    return True


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _auto_ai_enrich_new_item_window_hours() -> int:
    try:
        return max(0, int(settings.ai_auto_enrich_new_item_max_age_hours))
    except (TypeError, ValueError):
        return 24


def _auto_ai_enrich_new_item_cutoff(now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    return current_time - timedelta(hours=_auto_ai_enrich_new_item_window_hours())


def _item_is_recent_auto_ai_enrichment_candidate(
    item: Item, *, now: datetime | None = None
) -> bool:
    cutoff = _auto_ai_enrich_new_item_cutoff(now)
    published_at = _coerce_utc(item.published_at)
    first_seen_at = _coerce_utc(item.first_seen_at)
    if published_at is None or first_seen_at is None:
        return False
    return published_at >= cutoff and first_seen_at >= cutoff


def _get_ai_run_stop_reason(run_id: uuid.UUID | None) -> str | None:
    if run_id is None:
        return None
    with db_session() as db:
        return get_ai_task_run_stop_reason(db, run_id=run_id)


def _record_skipped_item_ai_enrichment_run(
    *,
    item_id: uuid.UUID,
    trigger_source: str,
    reason: str,
    actor_user_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> uuid.UUID:
    with db_session() as db:
        run = queue_ai_task_run(
            db,
            task_type=AI_TASK_TYPE_ITEM_ENRICHMENT,
            trigger_source=trigger_source,
            actor_user_id=actor_user_id,
            item_id=item_id,
            parent_run_id=parent_run_id,
            model=model,
            metadata=metadata,
            reason=reason,
        )
        ai_ops.finish_ai_task_run(
            db,
            run_id=run.id,
            status=ai_ops.AI_STATUS_SKIPPED,
            reason=reason,
            metadata_updates=metadata,
        )
        db.commit()
        return run.id


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_feeds")
def dispatch_due_feeds():
    return _dispatch_due_feeds(
        db_session_factory=db_session,
        settings=settings,
        claim_feed_for_dispatch=_claim_feed_for_dispatch,
        fetch_feed_task=fetch_feed,
        clear_feed_dispatch_claim=feed_pipeline.clear_feed_dispatch_claim,
        next_feed_fetch_at=_next_feed_fetch_at,
        logger=logger,
    )


@celery_app.task(name="app.tasks.feed_tasks.dispatch_unclassified_items")
def dispatch_unclassified_items():
    from app.tasks.processing_tasks import dispatch_processing_work

    return dispatch_processing_work(stage="classification")


@celery_app.task(name="app.tasks.feed_tasks.dispatch_items_missing_articles")
def dispatch_items_missing_articles():
    from app.tasks.processing_tasks import dispatch_processing_work

    return dispatch_processing_work(stage="article")


@celery_app.task(name="app.tasks.feed_tasks.dispatch_items_missing_iocs")
def dispatch_items_missing_iocs():
    from app.tasks.processing_tasks import dispatch_processing_work

    return dispatch_processing_work(stage="ioc")


@celery_app.task(name="app.tasks.feed_tasks.dispatch_items_missing_ai_enrichment")
def dispatch_items_missing_ai_enrichment():
    return _dispatch_items_missing_ai_enrichment(
        db_session_factory=db_session,
        settings=settings,
        load_active_ai_settings=lambda db: ai_config.load_active_ai_settings(db, feature_type="item_enrichment"),
        reconcile_stale_ai_runs=_reconcile_stale_ai_runs,
        auto_enrich_cutoff=_auto_ai_enrich_new_item_cutoff,
        auto_enrich_window_hours=_auto_ai_enrich_new_item_window_hours,
        safe_queue_item_ai_enrichment_run=_safe_queue_item_ai_enrichment_run,
        trigger_source=ai_ops.AI_TRIGGER_AUTO,
    )


@celery_app.task(name="app.tasks.feed_tasks.dispatch_feed_metadata_backfill")
def dispatch_feed_metadata_backfill():
    return _dispatch_feed_metadata_backfill(
        db_session_factory=db_session,
        settings=settings,
        needs_feed_metadata_backfill=feed_metadata.needs_feed_metadata_backfill,
        backfill_feed_metadata_task=backfill_feed_metadata,
        logger=logger,
    )


@celery_app.task(name="app.tasks.feed_tasks.record_beat_heartbeat")
def record_beat_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    try:
        redis_client.set(
            settings.beat_heartbeat_key, now, ex=settings.beat_heartbeat_ttl_seconds
        )
    except redis.RedisError as exc:
        logger.warning(
            "beat_heartbeat_write_failed error_type=%s",
            feed_task_runtime.exception_type_name(exc),
        )
        return {"status": "error", "reason": "redis_unavailable"}
    return {"status": "ok", "at": now}


@celery_app.task(
    name="app.tasks.feed_tasks.backfill_feed_metadata",
    acks_late=True,
    reject_on_worker_lost=True,
)
def backfill_feed_metadata(feed_id: str):
    return _run_backfill_feed_metadata(feed_id, dependencies=_feed_fetch_dependencies())


@celery_app.task(
    name="app.tasks.feed_tasks.fetch_feed",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def fetch_feed(self, feed_id: str, force: bool = False):
    return _run_fetch_feed(
        self, feed_id, force, dependencies=_feed_fetch_dependencies()
    )


@celery_app.task(
    name="app.tasks.feed_tasks.fetch_article",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def fetch_article(self, item_id: str, force: bool = False):
    return _run_fetch_article(
        self, item_id, force, dependencies=_article_fetch_dependencies()
    )


@celery_app.task(
    name="app.tasks.feed_tasks.classify_item",
    acks_late=True,
    reject_on_worker_lost=True,
)
def classify_item(item_id: str):
    return _run_classify_item(item_id, dependencies=_item_processing_dependencies())


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.generate_item_ai_enrichment",
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_item_ai_enrichment_task(
    self,
    item_id: str,
    force: bool = False,
    task_run_id: str | None = None,
):
    return _run_generate_item_ai_enrichment(
        self,
        item_id,
        force,
        task_run_id,
        dependencies=_item_ai_dependencies(),
    )


def _parse_uuid_text_list(values: list[str] | None) -> list[uuid.UUID]:
    return _parse_uuid_text_list_impl(values)


def _parse_datetime_text(value: str | None) -> datetime | None:
    return _parse_datetime_text_impl(value)


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.reprocess_recent_ai_items",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reprocess_recent_ai_items(
    self,
    days: int | None,
    limit: int,
    start_time: str | None = None,
    end_time: str | None = None,
    feed_ids: list[str] | None = None,
    item_ids: list[str] | None = None,
    task_run_id: str | None = None,
    actor_user_id: str | None = None,
):
    return _run_reprocess_recent_ai_items(
        self,
        days,
        limit,
        start_time,
        end_time,
        feed_ids,
        item_ids,
        task_run_id,
        actor_user_id,
        dependencies=_item_ai_dependencies(),
    )


@celery_app.task(
    name="app.tasks.feed_tasks.extract_item_iocs",
    acks_late=True,
    reject_on_worker_lost=True,
)
def extract_item_iocs(item_id: str):
    return _run_extract_item_iocs(item_id, dependencies=_item_processing_dependencies())


@celery_app.task(
    name="app.tasks.feed_tasks.reapply_recent_item_tags",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reapply_recent_item_tags(
    days: int = 30, limit: int = 0, dispatch_token: str | None = None
):
    return _run_reapply_recent_item_tags(
        days,
        limit,
        dispatch_token,
        dependencies=_item_processing_dependencies(),
    )


@celery_app.task(
    name="app.tasks.feed_tasks.repair_pending_item_tags",
    acks_late=True,
    reject_on_worker_lost=True,
)
def repair_pending_item_tags():
    from app.tasks.processing_tasks import dispatch_processing_work

    return dispatch_processing_work(stage="tagging")


def _feed_fetch_dependencies() -> FeedFetchDependencies:
    return FeedFetchDependencies(
        db_session=db_session,
        settings=FeedFetchOptions.from_settings(settings),
        enqueue_articles=enqueue_article_fetch_processing,
    )


def _article_fetch_dependencies() -> ArticleFetchDependencies:
    return ArticleFetchDependencies(
        db_session=db_session,
        settings=ArticleFetchOptions.from_settings(settings),
        enqueue_classification=_enqueue_classification_task,
    )


def _item_processing_dependencies() -> ItemProcessingDependencies:
    return ItemProcessingDependencies(
        db_session=db_session,
        enqueue_iocs=_safe_enqueue_item_iocs,
        queue_ai_enrichment=_safe_queue_item_ai_enrichment_run,
        record_skipped_ai_enrichment=_record_skipped_item_ai_enrichment_run,
        is_recent_ai_candidate=_item_is_recent_auto_ai_enrichment_candidate,
    )


def _item_ai_dependencies() -> ItemAIDependencies:
    return ItemAIDependencies(
        db_session=db_session,
        queue_ai_enrichment=_safe_queue_item_ai_enrichment_run,
        ai_run_stop_reason=_get_ai_run_stop_reason,
    )
