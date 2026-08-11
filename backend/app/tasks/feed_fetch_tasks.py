import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType

import httpx
from sqlalchemy import select

from app.models.feed import Feed
from app.models.item import Item


@dataclass(frozen=True)
class FeedFetchResponse:
    body: bytes
    etag: str | None
    last_modified: str | None
    final_url: str


def run_backfill_feed_metadata(feed_id: str, *, runtime: ModuleType):
    r = runtime
    try:
        with r.feed_lock(feed_id) as acquired:
            if not acquired:
                return {
                    "status": "skipped",
                    "reason": "already_fetching",
                    "feed_id": feed_id,
                }

            with r.db_session() as db:
                parsed_feed_id = _parse_uuid(feed_id)
                if parsed_feed_id is None:
                    return {
                        "status": "skipped",
                        "reason": "invalid_feed_id",
                        "feed_id": feed_id,
                    }
                feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
                if feed is None:
                    return {
                        "status": "skipped",
                        "reason": "not_found_or_disabled",
                        "feed_id": feed_id,
                    }
                if not feed.enabled:
                    return {
                        "status": "skipped",
                        "reason": "not_found_or_disabled",
                        "feed_id": feed_id,
                    }
                if feed.url_decryption_error:
                    r._mark_feed_failure_and_enqueue_notifications(
                        db, feed, feed.url_decryption_error
                    )
                    return {
                        "status": "error",
                        "feed_id": feed_id,
                        "reason": "feed_url_unavailable",
                    }
                if not r._needs_feed_metadata_backfill(feed):
                    return {
                        "status": "skipped",
                        "reason": "metadata_present",
                        "feed_id": feed_id,
                    }

                feed_url, feed_url_error = r._resolve_feed_runtime_url(feed)
                if feed_url_error is not None:
                    r._mark_feed_failure_and_enqueue_notifications(
                        db, feed, feed_url_error
                    )
                    return {
                        "status": "error",
                        "feed_id": feed_id,
                        "reason": "feed_url_unavailable",
                    }
                try:
                    metadata = r.probe_feed_metadata(feed_url)
                except r.FeedProbeError as exc:
                    return {"status": "error", "feed_id": feed_id, "reason": str(exc)}

                changed = r._apply_probe_metadata(feed, metadata)
                if changed:
                    db.add(feed)
                    db.commit()
                return {"status": "ok", "feed_id": feed_id, "updated": changed}
    except r.CoordinationUnavailableError as exc:
        r.logger.warning(
            "backfill_feed_metadata_coordination_unavailable feed_id=%s error_type=%s",
            feed_id,
            r._exception_type_name(exc),
        )
        return {
            "status": "error",
            "reason": "coordination_unavailable",
            "feed_id": feed_id,
        }


def run_fetch_feed(task, feed_id: str, force: bool = False, *, runtime: ModuleType):
    r = runtime
    try:
        with r.feed_lock(feed_id) as acquired:
            if not acquired:
                return {
                    "status": "skipped",
                    "reason": "already_fetching",
                    "feed_id": feed_id,
                }
            return _fetch_locked_feed(task, feed_id, force, runtime=r)
    except r.CoordinationUnavailableError as exc:
        return _recover_coordination_failure(task, feed_id, exc, runtime=r)


def _fetch_locked_feed(task, feed_id: str, force: bool, *, runtime: ModuleType):
    r = runtime
    integration_event_ids: list[uuid.UUID] = []
    with r.db_session() as db:
        parsed_feed_id = _parse_uuid(feed_id)
        if parsed_feed_id is None:
            return {
                "status": "skipped",
                "reason": "invalid_feed_id",
                "feed_id": feed_id,
            }

        feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
        if feed is None or not feed.enabled:
            _clear_disabled_feed_schedule(db, feed, runtime=r)
            return {
                "status": "skipped",
                "reason": "not_found_or_disabled",
                "feed_id": feed_id,
            }

        now = datetime.now(timezone.utc)
        is_retry_attempt = int(getattr(task.request, "retries", 0) or 0) > 0
        if not force and not is_retry_attempt and not r._is_feed_due(feed, now):
            r._clear_feed_dispatch_claim(feed)
            r._refresh_feed_next_fetch_at(feed, now)
            db.add(feed)
            db.commit()
            return {"status": "skipped", "reason": "not_due", "feed_id": feed_id}

        target = _resolve_fetch_target(db, feed, feed_id, runtime=r)
        if isinstance(target, dict):
            return target
        feed_url, feed_url_digest = target
        response = _request_feed(
            task,
            db,
            feed,
            feed_id,
            parsed_feed_id,
            feed_url,
            feed_url_digest,
            runtime=r,
        )
        if isinstance(response, dict):
            return response
        stored = _store_feed_response(
            db,
            feed,
            parsed_feed_id,
            feed_url_digest,
            response,
            integration_event_ids,
            feed_id=feed_id,
            runtime=r,
        )
        if isinstance(stored, dict):
            return stored
        changed_item_ids, new_items = stored

    article_enqueue_ok = r.enqueue_article_fetch_processing(changed_item_ids)
    notification_enqueue_ok = r.enqueue_integration_event_routing(integration_event_ids)
    return {
        "status": "ok",
        "feed_id": feed_id,
        "new_or_updated_items": len(changed_item_ids),
        "new_items": len(new_items),
        "final_url": response.final_url,
        "article_enqueue_failed": bool(changed_item_ids) and not article_enqueue_ok,
        "notification_deliveries_reserved": len(integration_event_ids),
        "notification_enqueue_failed": bool(integration_event_ids)
        and not notification_enqueue_ok,
        "smtp_notifications_queued": len(integration_event_ids),
        "smtp_notification_enqueue_failed": bool(integration_event_ids)
        and not notification_enqueue_ok,
    }


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _clear_disabled_feed_schedule(
    db, feed: Feed | None, *, runtime: ModuleType
) -> None:
    if feed is None:
        return
    runtime._clear_feed_dispatch_claim(feed)
    feed.next_fetch_at = None
    db.add(feed)
    db.commit()


def _resolve_fetch_target(db, feed: Feed, feed_id: str, *, runtime: ModuleType):
    r = runtime
    feed_url, feed_url_error = r._resolve_feed_runtime_url(feed)
    if feed_url_error is not None:
        r._mark_feed_failure_and_enqueue_notifications(db, feed, feed_url_error)
        return {"status": "error", "feed_id": feed_id, "reason": "feed_url_unavailable"}
    if not r.is_fetchable_url(
        feed_url, allow_private_network=r.settings.allow_private_network_fetch
    ):
        r._mark_feed_failure_and_enqueue_notifications(db, feed, "unsafe_feed_url")
        return {"status": "error", "feed_id": feed_id}
    return feed_url, feed.url_digest


def _request_feed(
    task,
    db,
    feed: Feed,
    feed_id: str,
    parsed_feed_id: uuid.UUID,
    feed_url: str,
    feed_url_digest: str,
    *,
    runtime: ModuleType,
):
    r = runtime
    headers = _conditional_request_headers(feed)
    try:
        response = _read_feed_response(feed_url, headers, runtime=r)
    except r.CoordinationUnavailableError as exc:
        return _retry_feed_exception(
            task,
            db,
            feed,
            feed_id,
            parsed_feed_id,
            feed_url_digest,
            exc,
            coordination=True,
            runtime=r,
        )
    except (httpx.HTTPError, r.SafeFetchError, r.RedirectError, TimeoutError) as exc:
        return _retry_feed_exception(
            task,
            db,
            feed,
            feed_id,
            parsed_feed_id,
            feed_url_digest,
            exc,
            coordination=False,
            runtime=r,
        )
    except r.FeedResponseTooLargeError as exc:
        if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
            db.rollback()
            return {
                "status": "skipped",
                "reason": "feed_url_changed",
                "feed_id": feed_id,
            }
        r.logger.error(
            "feed_fetch_too_large feed_id=%s error_type=%s",
            feed_id,
            r._exception_type_name(exc),
        )
        r._mark_feed_failure_and_enqueue_notifications(
            db, feed, "feed_response_too_large"
        )
        return {"status": "error", "feed_id": feed_id}

    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}
    if response == "not_modified":
        _record_not_modified(db, feed, runtime=r)
        return {"status": "not_modified", "feed_id": feed_id}
    if isinstance(response, tuple):
        status_code, final_url = response
        r._mark_feed_failure_and_enqueue_notifications(
            db, feed, f"http_status:{status_code}"
        )
        return {"status": "error", "feed_id": feed_id}
    return response


def _read_feed_response(feed_url: str, headers: dict[str, str], *, runtime: ModuleType):
    r = runtime
    timeout = httpx.Timeout(
        connect=r.settings.feed_connect_timeout_seconds,
        read=r.settings.feed_read_timeout_seconds,
        write=r.settings.feed_read_timeout_seconds,
        pool=r.settings.feed_connect_timeout_seconds,
    )
    with r.build_safe_http_client(
        timeout=timeout,
        headers={"User-Agent": r.settings.fetch_user_agent},
        allow_private_network=r.settings.allow_private_network_fetch,
    ) as client:
        response = r.safe_stream_with_redirects(
            client,
            "GET",
            feed_url,
            headers=headers,
            allow_private_network=r.settings.allow_private_network_fetch,
            max_redirects=r.settings.outbound_max_redirects,
        )
        try:
            if response.status_code == 304:
                return "not_modified"
            if response.status_code != 200:
                return response.status_code, str(response.url)
            body = _read_capped_body(
                response, r.settings.feed_max_bytes, r.FeedResponseTooLargeError
            )
            return FeedFetchResponse(
                body=body,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                final_url=str(response.url),
            )
        finally:
            response.close()


def _read_capped_body(
    response, max_bytes: int, too_large_error: type[Exception]
) -> bytes:
    body_chunks: list[bytes] = []
    body_size = 0
    for chunk in response.iter_bytes():
        body_size += len(chunk)
        if body_size > max_bytes:
            raise too_large_error("feed response exceeds configured cap")
        body_chunks.append(chunk)
    return b"".join(body_chunks)


def _conditional_request_headers(feed: Feed) -> dict[str, str]:
    headers: dict[str, str] = {}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified
    return headers


def _retry_feed_exception(
    task,
    db,
    feed: Feed,
    feed_id: str,
    parsed_feed_id: uuid.UUID,
    feed_url_digest: str,
    exc: Exception,
    *,
    coordination: bool,
    runtime: ModuleType,
):
    r = runtime
    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}
    error_code = (
        "coordination_unavailable"
        if coordination
        else r._safe_feed_fetch_error_code(exc)
    )
    r.logger.warning(
        "feed_fetch_retrying feed_id=%s retries=%s error_code=%s error_type=%s",
        feed_id,
        task.request.retries,
        error_code,
        r._exception_type_name(exc),
    )
    try:
        raise task.retry(
            exc=exc, countdown=min(2**task.request.retries, 300), max_retries=3
        )
    except r.MaxRetriesExceededError:
        if coordination:
            r.logger.error(
                "feed_fetch_coordination_retries_exhausted feed_id=%s error_type=%s",
                feed_id,
                r._exception_type_name(exc),
            )
            r._reschedule_feed_after_coordination_failure(db, feed)
            return {
                "status": "error",
                "feed_id": feed_id,
                "reason": "coordination_unavailable",
            }
        r.logger.error(
            "feed_fetch_failed feed_id=%s error_code=%s error_type=%s",
            feed_id,
            error_code,
            r._exception_type_name(exc),
        )
        r._mark_feed_failure_and_enqueue_notifications(db, feed, error_code)
        return {"status": "error", "feed_id": feed_id}


def _store_feed_response(
    db,
    feed: Feed,
    parsed_feed_id: uuid.UUID,
    feed_url_digest: str,
    response: FeedFetchResponse,
    integration_event_ids: list[uuid.UUID],
    *,
    feed_id: str,
    runtime: ModuleType,
):
    r = runtime
    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}
    try:
        parsed_items, _ = r.RSSConnector().poll({"body": response.body}, None)
    except r.RSSFeedParseError as exc:
        if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
            db.rollback()
            return {
                "status": "skipped",
                "reason": "feed_url_changed",
                "feed_id": feed_id,
            }
        r.logger.warning(
            "feed_fetch_invalid_content feed_id=%s error_type=%s",
            feed_id,
            r._exception_type_name(exc),
        )
        r._mark_feed_failure_and_enqueue_notifications(db, feed, "invalid_feed_content")
        return {"status": "error", "feed_id": feed_id}
    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}

    r._backfill_feed_metadata_from_body(feed, response.body)
    changed_item_ids, new_items = _upsert_parsed_items(
        db, feed, parsed_items, runtime=r
    )
    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}
    for new_item in new_items:
        integration_event_ids.append(
            r._emit_item_integration_event(
                db, event_type="rss_item_new", item=new_item, feed=feed
            )
        )
    if _feed_url_changed(db, parsed_feed_id, feed_url_digest, runtime=r):
        db.rollback()
        return {"status": "skipped", "reason": "feed_url_changed", "feed_id": feed_id}
    _record_feed_success(db, feed, response, runtime=r)
    return changed_item_ids, new_items


def _upsert_parsed_items(db, feed: Feed, parsed_items, *, runtime: ModuleType):
    changed_item_ids: list[uuid.UUID] = []
    new_items: list[Item] = []
    for parsed in parsed_items:
        item, changed, is_new = runtime._upsert_item_from_parsed(db, feed, parsed)
        if changed:
            changed_item_ids.append(item.id)
        if is_new:
            new_items.append(item)
    return changed_item_ids, new_items


def _feed_url_changed(
    db, feed_id: uuid.UUID, digest: str, *, runtime: ModuleType
) -> bool:
    return not runtime._feed_url_digest_still_current(
        db, feed_id=feed_id, expected_url_digest=digest
    )


def _record_not_modified(db, feed: Feed, *, runtime: ModuleType) -> None:
    now = datetime.now(timezone.utc)
    feed.last_fetch_at = now
    feed.last_success_at = now
    feed.error_count = 0
    feed.last_error = None
    runtime._clear_feed_dispatch_claim(feed)
    runtime._refresh_feed_next_fetch_at(feed, now)
    db.add(feed)
    db.commit()


def _record_feed_success(
    db, feed: Feed, response: FeedFetchResponse, *, runtime: ModuleType
) -> None:
    now = datetime.now(timezone.utc)
    feed.etag = response.etag or feed.etag
    feed.last_modified = response.last_modified or feed.last_modified
    feed.last_success_at = now
    feed.last_fetch_at = now
    feed.error_count = 0
    feed.last_error = None
    runtime._clear_feed_dispatch_claim(feed)
    runtime._refresh_feed_next_fetch_at(feed, now)
    db.add(feed)
    db.commit()


def _recover_coordination_failure(
    task, feed_id: str, exc: Exception, *, runtime: ModuleType
):
    r = runtime
    r.logger.warning(
        "feed_fetch_coordination_unavailable feed_id=%s error_type=%s",
        feed_id,
        r._exception_type_name(exc),
    )
    try:
        raise task.retry(
            exc=exc, countdown=min(2**task.request.retries, 300), max_retries=3
        )
    except r.MaxRetriesExceededError:
        with r.db_session() as db:
            parsed_feed_id = _parse_uuid(feed_id)
            if parsed_feed_id is None:
                return {"status": "error", "feed_id": feed_id}
            feed = db.scalar(select(Feed).where(Feed.id == parsed_feed_id))
            if feed is not None:
                r._reschedule_feed_after_coordination_failure(db, feed)
        return {
            "status": "error",
            "feed_id": feed_id,
            "reason": "coordination_unavailable",
        }
