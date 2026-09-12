from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.bounded_response import ResponseBodyTooLarge, read_bounded_response
from app.services.feed_input import FeedInputError, clean_feed_text, parse_feed_document, safe_feed_cache_header
from app.services.outbound_deadline import outbound_deadline
from app.services.safe_fetch import (
    RedirectError,
    SafeFetchError,
    build_safe_http_client,
    safe_fetch_request_guard,
    safe_stream_with_redirects,
)
from app.services.url_utils import is_fetchable_url


@dataclass
class FeedProbeResult:
    name: str | None
    description: str | None
    site_url: str | None
    language: str | None
    etag: str | None
    last_modified: str | None
    resolved_url: str | None
    feed_type: str | None


class FeedProbeError(RuntimeError):
    pass


class FeedProbeCoordinationError(FeedProbeError):
    pass


def probe_feed_metadata(
    url: str,
    *,
    request_context: Callable[[str], AbstractContextManager[Any]] | None = None,
    request_guard_validator: Callable[[object], None] | None = None,
) -> FeedProbeResult:
    settings = get_settings()
    target_url = url.strip()

    if not is_fetchable_url(target_url, allow_private_network=settings.allow_private_network_fetch):
        raise FeedProbeError("Feed URL is not allowed")

    timeout = httpx.Timeout(
        connect=settings.feed_connect_timeout_seconds,
        read=settings.feed_read_timeout_seconds,
        write=settings.feed_read_timeout_seconds,
        pool=settings.feed_connect_timeout_seconds,
    )

    try:
        with outbound_deadline(settings.feed_total_timeout_seconds), build_safe_http_client(
            timeout=timeout,
            headers={"User-Agent": settings.fetch_user_agent},
            allow_private_network=settings.allow_private_network_fetch,
        ) as client:
            response = safe_stream_with_redirects(
                client,
                "GET",
                target_url,
                allow_private_network=settings.allow_private_network_fetch,
                max_redirects=settings.outbound_max_redirects,
                request_context=request_context,
            )
            try:
                request_guard = safe_fetch_request_guard(response)
                _validate_request_guard(request_guard, request_guard_validator)
                if response.status_code != 200:
                    raise FeedProbeError(f"Feed returned HTTP {response.status_code}")

                etag = safe_feed_cache_header(response.headers.get("etag"))
                last_modified = safe_feed_cache_header(response.headers.get("last-modified"))
                resolved_url = str(response.url)

                body = read_bounded_response(
                    response,
                    settings.feed_max_bytes,
                    check=lambda: _validate_request_guard(request_guard, request_guard_validator),
                )
            finally:
                response.close()
    except ResponseBodyTooLarge as exc:
        raise FeedProbeError("Feed response exceeds configured size limit") from exc
    except (httpx.HTTPError, SafeFetchError, RedirectError) as exc:
        raise FeedProbeError(f"Unable to fetch feed: {exc}") from exc

    try:
        parsed = parse_feed_document(body)
    except FeedInputError as exc:
        raise FeedProbeError("The publisher did not return a valid feed document. Check the feed URL or try again after the publisher fixes its content.") from exc
    metadata = parsed.feed if hasattr(parsed, "feed") else {}

    title = clean_feed_text(metadata.get("title"))
    description = clean_feed_text(metadata.get("subtitle") or metadata.get("description"))
    site_url = clean_feed_text(metadata.get("link"))
    language = clean_feed_text(metadata.get("language"), max_chars=64)

    return FeedProbeResult(
        name=title,
        description=description,
        site_url=site_url,
        language=language,
        etag=etag,
        last_modified=last_modified,
        resolved_url=resolved_url,
        feed_type=clean_feed_text(getattr(parsed, "version", None)),
    )


def _validate_request_guard(
    request_guard: object | None,
    validator: Callable[[object], None] | None,
) -> None:
    if validator is not None and request_guard is not None:
        validator(request_guard)
