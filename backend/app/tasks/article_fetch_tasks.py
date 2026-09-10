import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select

from app.models.article import Article
from app.models.item import Item
from app.services import extraction, safe_fetch, url_utils
from app.services.article_recovery import lock_article_feed
from app.services.bounded_response import read_bounded_response
from app.services.classification_recovery import require_item_classification
from app.services.outbound_deadline import outbound_deadline
from app.tasks import feed_task_coordination, feed_task_runtime, feed_task_storage
from app.tasks.feed_task_dependencies import ArticleFetchDependencies

logger = logging.getLogger(__name__)


ARTICLE_FETCH_MAX_RETRIES = 3


@dataclass(frozen=True)
class ArticleFetchResult:
    final_url: str
    http_status: int
    content_type: str | None
    body: bytes = b""
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def run_fetch_article(
    task,
    item_id: str,
    force: bool = False,
    *,
    dependencies: ArticleFetchDependencies,
    source_access_fenced: bool = False,
):
    with dependencies.db_session() as db:
        parsed_item_id = _parse_uuid(item_id)
        if parsed_item_id is None:
            return {
                "status": "skipped",
                "reason": "invalid_item_id",
                "item_id": item_id,
            }

        feed = None
        if not force and not source_access_fenced:
            # Initial-ingestion messages may already be queued when the feed is
            # disabled. Feed precedes Item in the shared source lock order.
            feed = lock_article_feed(db, parsed_item_id)
            if feed is None or not feed.enabled:
                return {
                    "status": "skipped",
                    "reason": "not_found" if feed is None else "feed_disabled",
                    "item_id": item_id,
                }
        item, skip_result = _load_claimed_item(db, parsed_item_id, item_id)
        if skip_result is not None:
            return skip_result
        if feed is not None and item.feed_id != feed.feed_id:
            return {"status": "skipped", "reason": "source_changed", "item_id": item_id}
        cached_result = _cached_article_result(
            db, item, item_id, force, dependencies=dependencies
        )
        if cached_result is not None:
            return cached_result

        candidate_urls = _candidate_urls(item)
        if not candidate_urls:
            return _record_missing_url(db, item, item_id, dependencies=dependencies)

        started_at = time.perf_counter()
        with outbound_deadline(dependencies.settings.article_total_timeout_seconds):
            result = _fetch_candidates(
                task, item_id, candidate_urls, dependencies=dependencies
            )
        fetch_ms = int((time.perf_counter() - started_at) * 1000)
        if not result.succeeded:
            feed_task_storage.store_article_error(
                db,
                item,
                final_url=result.final_url,
                http_status=result.http_status,
                content_type=result.content_type,
                fetch_ms=fetch_ms,
                error=result.error or "article_fetch_failed",
            )
            dependencies.enqueue_classification(item_id)
            return feed_task_storage.article_fetch_error_result(item, item_id)

        _store_article_success(db, item, result, fetch_ms, dependencies=dependencies)

    dependencies.enqueue_classification(item_id)
    return {"status": "ok", "item_id": item_id}


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _load_claimed_item(db, parsed_item_id: uuid.UUID, item_id: str):
    item = db.scalar(
        select(Item).where(Item.id == parsed_item_id).with_for_update(skip_locked=True)
    )
    if item is not None:
        return item, None
    unlocked_item = db.scalar(select(Item).where(Item.id == parsed_item_id))
    if unlocked_item is None:
        return None, {"status": "skipped", "reason": "not_found", "item_id": item_id}
    return None, {
        "status": "skipped",
        "reason": "concurrent_fetch_in_progress",
        "item_id": item_id,
    }


def _cached_article_result(
    db, item: Item, item_id: str, force: bool, *, dependencies: ArticleFetchDependencies
):
    existing_article = db.scalar(select(Article).where(Article.item_id == item.id))
    if (
        existing_article is not None
        and existing_article.content_purged_at is not None
        and not force
    ):
        return {
            "status": "skipped",
            "reason": "content_purged_by_lifecycle",
            "item_id": item_id,
        }
    if existing_article is None or item.status != "content_fetched" or force:
        return None
    if not existing_article.text:
        return None
    dependencies.enqueue_classification(item_id)
    reason = (
        "already_fetched" if not existing_article.error else "degraded_article_cached"
    )
    return {"status": "skipped", "reason": reason, "item_id": item_id}


def _candidate_urls(item: Item) -> list[str]:
    candidates: list[str] = []
    for candidate in (item.canonical_url, item.url):
        if not candidate:
            continue
        normalized = url_utils.normalize_url(candidate)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _record_missing_url(
    db, item: Item, item_id: str, *, dependencies: ArticleFetchDependencies
):
    feed_task_storage.store_article_error(
        db,
        item,
        final_url="",
        http_status=0,
        content_type=None,
        fetch_ms=0,
        error="missing_article_url",
    )
    dependencies.enqueue_classification(item_id)
    return feed_task_storage.article_fetch_error_result(item, item_id)


def _fetch_candidates(
    task,
    item_id: str,
    candidate_urls: list[str],
    *,
    dependencies: ArticleFetchDependencies,
) -> ArticleFetchResult:
    last_result = ArticleFetchResult(
        candidate_urls[0], 0, None, error="article_fetch_failed"
    )
    for index, target_url in enumerate(candidate_urls):
        has_fallback = index + 1 < len(candidate_urls)
        if not url_utils.is_fetchable_url(
            target_url,
            allow_private_network=dependencies.settings.allow_private_network_fetch,
        ):
            last_result = ArticleFetchResult(
                target_url, 0, None, error="unsafe_article_url"
            )
            continue
        try:
            result = _fetch_candidate(target_url, dependencies=dependencies)
        except (
            httpx.HTTPError,
            TimeoutError,
            safe_fetch.SafeFetchError,
            safe_fetch.RedirectError,
            feed_task_coordination.CoordinationUnavailableError,
        ) as exc:
            last_result = _retryable_failure(
                task,
                item_id,
                target_url,
                exc,
                has_fallback,
                candidate_urls,
                index,
            )
            if has_fallback:
                continue
            return last_result
        except feed_task_runtime.ResponseTooLargeError as exc:
            last_result = ArticleFetchResult(
                target_url, 0, None, error="response_too_large"
            )
            logger.error(
                "article_fetch_too_large item_id=%s target_url=%s error_type=%s",
                item_id,
                target_url,
                feed_task_runtime.exception_type_name(exc),
            )
            if has_fallback:
                _log_fallback(
                    item_id,
                    target_url,
                    candidate_urls[index + 1],
                    last_result.error,
                    exc,
                )
                continue
            return last_result

        if result.succeeded:
            return result
        last_result = result
        if has_fallback:
            logger.info(
                "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=%s",
                item_id,
                target_url,
                candidate_urls[index + 1],
                result.error,
            )
            continue
        return result
    return last_result


def _fetch_candidate(
    target_url: str, *, dependencies: ArticleFetchDependencies
) -> ArticleFetchResult:
    timeout = httpx.Timeout(
        connect=dependencies.settings.article_connect_timeout_seconds,
        read=dependencies.settings.article_read_timeout_seconds,
        write=dependencies.settings.article_read_timeout_seconds,
        pool=dependencies.settings.article_connect_timeout_seconds,
    )
    with (
        outbound_deadline(dependencies.settings.article_total_timeout_seconds),
        safe_fetch.build_safe_http_client(
            timeout=timeout,
            headers={"User-Agent": dependencies.settings.fetch_user_agent},
            allow_private_network=dependencies.settings.allow_private_network_fetch,
        ) as client,
    ):
        response = safe_fetch.safe_stream_with_redirects(
            client,
            "GET",
            target_url,
            allow_private_network=dependencies.settings.allow_private_network_fetch,
            max_redirects=dependencies.settings.outbound_max_redirects,
            request_context=lambda request_url: feed_task_coordination.domain_slot(
                urlsplit(request_url).hostname or "unknown"
            ),
        )
        lease = safe_fetch.safe_fetch_request_guard(response)
        try:
            feed_task_coordination.ensure_lease_owned(lease)
            status_code = response.status_code
            content_type = response.headers.get("content-type")
            final_url = url_utils.normalize_url(str(response.url)) or ""
            body = _read_capped_body(
                response,
                dependencies.settings.article_max_bytes,
                feed_task_runtime.ResponseTooLargeError,
                lease=lease,
                dependencies=dependencies,
            )
        finally:
            response.close()
    error = _response_error(status_code, content_type)
    return ArticleFetchResult(
        final_url, status_code, content_type, body=body, error=error
    )


def _read_capped_body(
    response,
    max_bytes: int,
    too_large_error: type[Exception],
    *,
    lease=None,
    dependencies: ArticleFetchDependencies | None = None,
) -> bytes:
    def check():
        if dependencies is not None:
            feed_task_coordination.ensure_lease_owned(lease)

    return read_bounded_response(
        response, max_bytes, check=check, too_large_error=too_large_error
    )


def _response_error(status_code: int, content_type: str | None) -> str | None:
    if status_code != 200:
        return f"http_status:{status_code}"
    if "text/html" not in (content_type or "").lower():
        return "non_html_response"
    return None


def _retryable_failure(
    task,
    item_id: str,
    target_url: str,
    exc: Exception,
    has_fallback: bool,
    candidate_urls: list[str],
    index: int,
) -> ArticleFetchResult:
    error_code = feed_task_runtime.safe_article_fetch_error_code(exc)
    if has_fallback:
        _log_fallback(
            item_id,
            target_url,
            candidate_urls[index + 1],
            error_code,
            exc,
        )
        return ArticleFetchResult(target_url, 0, None, error=error_code)
    if int(getattr(task.request, "retries", 0) or 0) >= ARTICLE_FETCH_MAX_RETRIES:
        logger.error(
            "article_fetch_failed item_id=%s error_code=%s error_type=%s",
            item_id,
            error_code,
            feed_task_runtime.exception_type_name(exc),
        )
        return ArticleFetchResult(target_url, 0, None, error=error_code)
    logger.warning(
        "article_fetch_retrying item_id=%s retries=%s error_code=%s error_type=%s",
        item_id,
        task.request.retries,
        error_code,
        feed_task_runtime.exception_type_name(exc),
    )
    raise task.retry(
        exc=exc,
        countdown=min(2 ** int(task.request.retries or 0), 300),
        max_retries=ARTICLE_FETCH_MAX_RETRIES,
    )


def _log_fallback(
    item_id: str, from_url: str, to_url: str, error_code: str, exc: Exception
) -> None:
    logger.info(
        "article_fetch_fallback item_id=%s from_url=%s to_url=%s error_code=%s error_type=%s",
        item_id,
        from_url,
        to_url,
        error_code,
        feed_task_runtime.exception_type_name(exc),
    )


def _store_article_success(
    db,
    item: Item,
    result: ArticleFetchResult,
    fetch_ms: int,
    *,
    dependencies: ArticleFetchDependencies,
) -> None:
    html = result.body.decode("utf-8", errors="ignore")
    canonical = extraction.extract_canonical_url(html)
    if canonical:
        canonical = url_utils.normalize_url(urljoin(result.final_url, canonical))
    extracted = extraction.extract_readable_text(html)

    article = db.scalar(select(Article).where(Article.item_id == item.id))
    if article is None:
        article = Article(
            item_id=item.id, final_url=result.final_url, http_status=result.http_status
        )
    previous_text = article.text
    _apply_extracted_article(article, result, extracted, fetch_ms)

    if canonical and url_utils.is_fetchable_url(
        canonical,
        allow_private_network=dependencies.settings.allow_private_network_fetch,
    ):
        item.canonical_url = canonical
    item.url_domain = url_utils.extract_url_domain(item.canonical_url or item.url)
    _apply_item_fetch_state(article, item)
    _finalize_article_content_outcome(article)
    if article.text != previous_text:
        require_item_classification(item)
    db.add(article)
    db.add(item)
    db.commit()


def _apply_extracted_article(
    article: Article, result: ArticleFetchResult, extracted: dict, fetch_ms: int
) -> None:
    article.final_url = result.final_url
    article.retrieved_at = datetime.now(timezone.utc)
    article.http_status = result.http_status
    article.content_type = result.content_type
    article.title_extracted = extracted.get("title")
    article.text = extracted.get("text")
    article.extraction_method = extracted.get("method")
    article.language = extracted.get("language")
    article.word_count = extracted.get("word_count")
    article.fetch_ms = fetch_ms
    article.error = extracted.get("error")


def _apply_item_fetch_state(article: Article, item: Item) -> None:
    if _has_usable_article_text(article):
        item.status = "content_fetched"
        item.ioc_extraction_state = None
        item.last_error = None
        return
    if feed_task_storage.apply_article_summary_fallback(
        article, item, str(article.error or "no_extractor_succeeded")
    ):
        article.error = str(article.error or "no_extractor_succeeded")
        return
    item.status = "error"
    item.last_error = article.error


def _finalize_article_content_outcome(article: Article) -> None:
    if _has_usable_article_text(article):
        article.content_purged_at = None
        article.content_purge_run_id = None
        return
    if article.content_purged_at is None:
        return

    article.title_extracted = None
    article.text = None
    article.extraction_method = "retention_purged"
    article.language = None
    article.word_count = None


def _has_usable_article_text(article: Article) -> bool:
    return bool((article.text or "").strip())
