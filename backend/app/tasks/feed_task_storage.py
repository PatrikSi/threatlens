from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.ioc import IOC
from app.models.item import Item
from app.services.extraction import extract_plain_text


RSS_SUMMARY_FALLBACK_EXTRACTION_METHOD = "rss_summary_fallback"
RSS_SUMMARY_FALLBACK_HTTP_STATUSES = {401, 403, 404, 405, 410, 451}
RSS_SUMMARY_FALLBACK_EXACT_ERRORS = {
    "non_html_response",
    "no_extractor_succeeded",
    "response_too_large",
}
RSS_SUMMARY_FALLBACK_PREFIXES = ("readability_error:",)


def get_or_create_ioc(
    db: Session,
    *,
    ioc_type: str,
    ioc_value_norm: str,
    ioc_value_raw: str,
    now: datetime,
) -> IOC:
    ioc = db.scalar(select(IOC).where(IOC.type == ioc_type, IOC.value_norm == ioc_value_norm))
    if ioc is None:
        candidate = IOC(
            type=ioc_type,
            value_raw=ioc_value_raw,
            value_norm=ioc_value_norm,
            first_seen_at=now,
            last_seen_at=now,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate
        except IntegrityError:
            ioc = db.scalar(select(IOC).where(IOC.type == ioc_type, IOC.value_norm == ioc_value_norm))
            if ioc is None:
                raise

    ioc.last_seen_at = now
    db.add(ioc)
    db.flush()
    return ioc


def store_article_error(
    db: Session,
    item: Item,
    final_url: str,
    http_status: int,
    content_type: str | None,
    fetch_ms: int,
    error: str,
) -> None:
    article = db.scalar(select(Article).where(Article.item_id == item.id))
    if article is None:
        article = Article(item_id=item.id, final_url=final_url, http_status=http_status)

    article.final_url = final_url
    article.retrieved_at = datetime.now(timezone.utc)
    article.http_status = http_status
    article.content_type = content_type
    article.title_extracted = None
    article.language = None
    article.fetch_ms = fetch_ms
    article.error = error
    if not apply_article_summary_fallback(article, item, error):
        article.text = None
        article.extraction_method = "none"
        article.word_count = None
        item.status = "error"

    item.last_error = error

    db.add(article)
    db.add(item)
    db.commit()


def rss_summary_fallback_text(item: Item, error: str) -> str | None:
    if not article_error_allows_summary_fallback(error):
        return None

    raw_summary = (item.summary or "").strip()
    if not raw_summary:
        return None

    text = extract_plain_text(raw_summary)
    if not text:
        return None

    if item.title and text.strip().casefold() == item.title.strip().casefold():
        return None

    return text


def apply_article_summary_fallback(article: Article, item: Item, error: str) -> bool:
    fallback_text = rss_summary_fallback_text(item, error)
    if not fallback_text:
        return False

    article.title_extracted = item.title
    article.text = fallback_text
    article.extraction_method = RSS_SUMMARY_FALLBACK_EXTRACTION_METHOD
    article.word_count = len(fallback_text.split())
    item.status = "content_fetched"
    item.ioc_extraction_state = None
    item.last_error = error
    return True


def article_error_allows_summary_fallback(error: str) -> bool:
    if error in RSS_SUMMARY_FALLBACK_EXACT_ERRORS:
        return True

    if any(error.startswith(prefix) for prefix in RSS_SUMMARY_FALLBACK_PREFIXES):
        return True

    prefix, separator, raw_status = error.partition(":")
    if prefix != "http_status" or not separator:
        return False

    try:
        status_code = int(raw_status)
    except ValueError:
        return False

    return status_code in RSS_SUMMARY_FALLBACK_HTTP_STATUSES


def article_fetch_error_result(item: Item, item_id: str) -> dict[str, str]:
    if item.status == "content_fetched":
        return {
            "status": "degraded",
            "reason": RSS_SUMMARY_FALLBACK_EXTRACTION_METHOD,
            "item_id": item_id,
        }
    return {"status": "error", "item_id": item_id}
