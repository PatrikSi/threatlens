import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select

from app.models.article import Article
from app.models.item import Item


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


def run_fetch_article(task, item_id: str, force: bool = False, *, runtime: ModuleType):
    r = runtime
    with r.db_session() as db:
        parsed_item_id = _parse_uuid(item_id)
        if parsed_item_id is None:
            return {
                "status": "skipped",
                "reason": "invalid_item_id",
                "item_id": item_id,
            }

        item, skip_result = _load_claimed_item(db, parsed_item_id, item_id)
        if skip_result is not None:
            return skip_result
        cached_result = _cached_article_result(db, item, item_id, force, runtime=r)
        if cached_result is not None:
            return cached_result

        candidate_urls = _candidate_urls(item, runtime=r)
        if not candidate_urls:
            return _record_missing_url(db, item, item_id, runtime=r)

        started_at = time.perf_counter()
        result = _fetch_candidates(task, item_id, candidate_urls, runtime=r)
        fetch_ms = int((time.perf_counter() - started_at) * 1000)
        if not result.succeeded:
            r._store_article_error(
                db,
                item,
                final_url=result.final_url,
                http_status=result.http_status,
                content_type=result.content_type,
                fetch_ms=fetch_ms,
                error=result.error or "article_fetch_failed",
            )
            r._enqueue_classification_task(item_id)
            return r._article_fetch_error_result(item, item_id)

        _store_article_success(db, item, result, fetch_ms, runtime=r)

    r._enqueue_classification_task(item_id)
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
    db, item: Item, item_id: str, force: bool, *, runtime: ModuleType
):
    existing_article = db.scalar(select(Article).where(Article.item_id == item.id))
    if existing_article is None or item.status != "content_fetched" or force:
        return None
    if not existing_article.text:
        return None
    runtime._enqueue_classification_task(item_id)
    reason = (
        "already_fetched" if not existing_article.error else "degraded_article_cached"
    )
    return {"status": "skipped", "reason": reason, "item_id": item_id}


def _candidate_urls(item: Item, *, runtime: ModuleType) -> list[str]:
    candidates: list[str] = []
    for candidate in (item.canonical_url, item.url):
        if not candidate:
            continue
        normalized = runtime.normalize_url(candidate)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _record_missing_url(db, item: Item, item_id: str, *, runtime: ModuleType):
    runtime._store_article_error(
        db,
        item,
        final_url="",
        http_status=0,
        content_type=None,
        fetch_ms=0,
        error="missing_article_url",
    )
    runtime._enqueue_classification_task(item_id)
    return runtime._article_fetch_error_result(item, item_id)


def _fetch_candidates(
    task, item_id: str, candidate_urls: list[str], *, runtime: ModuleType
) -> ArticleFetchResult:
    r = runtime
    last_result = ArticleFetchResult(
        candidate_urls[0], 0, None, error="article_fetch_failed"
    )
    for index, target_url in enumerate(candidate_urls):
        has_fallback = index + 1 < len(candidate_urls)
        if not r.is_fetchable_url(
            target_url, allow_private_network=r.settings.allow_private_network_fetch
        ):
            last_result = ArticleFetchResult(
                target_url, 0, None, error="unsafe_article_url"
            )
            continue
        try:
            result = _fetch_candidate(target_url, runtime=r)
        except (
            httpx.HTTPError,
            TimeoutError,
            r.SafeFetchError,
            r.RedirectError,
            r.CoordinationUnavailableError,
        ) as exc:
            last_result = _retryable_failure(
                task,
                item_id,
                target_url,
                exc,
                has_fallback,
                candidate_urls,
                index,
                runtime=r,
            )
            if has_fallback:
                continue
            return last_result
        except r.ResponseTooLargeError as exc:
            last_result = ArticleFetchResult(
                target_url, 0, None, error="response_too_large"
            )
            r.logger.error(
                "article_fetch_too_large item_id=%s target_url=%s error_type=%s",
                item_id,
                target_url,
                r._exception_type_name(exc),
            )
            if has_fallback:
                _log_fallback(
                    item_id,
                    target_url,
                    candidate_urls[index + 1],
                    last_result.error,
                    exc,
                    runtime=r,
                )
                continue
            return last_result

        if result.succeeded:
            return result
        last_result = result
        if has_fallback:
            r.logger.info(
                "article_fetch_fallback item_id=%s from_url=%s to_url=%s reason=%s",
                item_id,
                target_url,
                candidate_urls[index + 1],
                result.error,
            )
            continue
        return result
    return last_result


def _fetch_candidate(target_url: str, *, runtime: ModuleType) -> ArticleFetchResult:
    r = runtime
    timeout = httpx.Timeout(
        connect=r.settings.article_connect_timeout_seconds,
        read=r.settings.article_read_timeout_seconds,
        write=r.settings.article_read_timeout_seconds,
        pool=r.settings.article_connect_timeout_seconds,
    )
    with r.build_safe_http_client(
        timeout=timeout,
        headers={"User-Agent": r.settings.fetch_user_agent},
        allow_private_network=r.settings.allow_private_network_fetch,
    ) as client:
        response = r.safe_stream_with_redirects(
            client,
            "GET",
            target_url,
            allow_private_network=r.settings.allow_private_network_fetch,
            max_redirects=r.settings.outbound_max_redirects,
            request_context=lambda request_url: r.domain_slot(
                urlsplit(request_url).hostname or "unknown"
            ),
        )
        lease = r.safe_fetch_request_guard(response)
        try:
            r.ensure_lease_owned(lease)
            status_code = response.status_code
            content_type = response.headers.get("content-type")
            final_url = r.normalize_url(str(response.url)) or ""
            body = _read_capped_body(
                response,
                r.settings.article_max_bytes,
                r.ResponseTooLargeError,
                lease=lease,
                runtime=r,
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
    runtime: ModuleType | None = None,
) -> bytes:
    chunks: list[bytes] = []
    body_size = 0
    for chunk in response.iter_bytes():
        if runtime is not None:
            runtime.ensure_lease_owned(lease)
        body_size += len(chunk)
        if body_size > max_bytes:
            raise too_large_error("response body exceeds configured cap")
        chunks.append(chunk)
    return b"".join(chunks)


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
    *,
    runtime: ModuleType,
) -> ArticleFetchResult:
    r = runtime
    error_code = r._safe_article_fetch_error_code(exc)
    if has_fallback:
        _log_fallback(
            item_id, target_url, candidate_urls[index + 1], error_code, exc, runtime=r
        )
        return ArticleFetchResult(target_url, 0, None, error=error_code)
    try:
        r.logger.warning(
            "article_fetch_retrying item_id=%s retries=%s error_code=%s error_type=%s",
            item_id,
            task.request.retries,
            error_code,
            r._exception_type_name(exc),
        )
        raise task.retry(
            exc=exc, countdown=min(2**task.request.retries, 300), max_retries=3
        )
    except r.MaxRetriesExceededError:
        r.logger.error(
            "article_fetch_failed item_id=%s error_code=%s error_type=%s",
            item_id,
            error_code,
            r._exception_type_name(exc),
        )
        return ArticleFetchResult(target_url, 0, None, error=error_code)


def _log_fallback(
    item_id: str,
    from_url: str,
    to_url: str,
    error_code: str,
    exc: Exception,
    *,
    runtime: ModuleType,
) -> None:
    runtime.logger.info(
        "article_fetch_fallback item_id=%s from_url=%s to_url=%s error_code=%s error_type=%s",
        item_id,
        from_url,
        to_url,
        error_code,
        runtime._exception_type_name(exc),
    )


def _store_article_success(
    db,
    item: Item,
    result: ArticleFetchResult,
    fetch_ms: int,
    *,
    runtime: ModuleType,
) -> None:
    r = runtime
    html = result.body.decode("utf-8", errors="ignore")
    canonical = r.extract_canonical_url(html)
    if canonical:
        canonical = r.normalize_url(urljoin(result.final_url, canonical))
    extracted = r.extract_readable_text(html)

    article = db.scalar(select(Article).where(Article.item_id == item.id))
    if article is None:
        article = Article(
            item_id=item.id, final_url=result.final_url, http_status=result.http_status
        )
    _apply_extracted_article(article, result, extracted, fetch_ms)

    if canonical and r.is_fetchable_url(
        canonical, allow_private_network=r.settings.allow_private_network_fetch
    ):
        item.canonical_url = canonical
    item.url_domain = r.extract_url_domain(item.canonical_url or item.url)
    _apply_item_fetch_state(article, item, runtime=r)
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


def _apply_item_fetch_state(
    article: Article, item: Item, *, runtime: ModuleType
) -> None:
    if article.text:
        item.status = "content_fetched"
        item.ioc_extraction_state = None
        item.last_error = None
        return
    if runtime._apply_article_summary_fallback(
        article, item, str(article.error or "no_extractor_succeeded")
    ):
        article.error = str(article.error or "no_extractor_succeeded")
        return
    item.status = "error"
    item.last_error = article.error
