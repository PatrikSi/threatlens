from dataclasses import dataclass

import feedparser
import httpx

from app.core.config import get_settings
from app.services.safe_fetch import RedirectError, SafeFetchError, safe_stream_with_redirects
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


def probe_feed_metadata(url: str) -> FeedProbeResult:
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
        with httpx.Client(timeout=timeout, headers={"User-Agent": settings.fetch_user_agent}) as client:
            response = safe_stream_with_redirects(
                client,
                "GET",
                target_url,
                allow_private_network=settings.allow_private_network_fetch,
                max_redirects=settings.outbound_max_redirects,
            )
            with response:
                if response.status_code != 200:
                    raise FeedProbeError(f"Feed returned HTTP {response.status_code}")

                body_chunks: list[bytes] = []
                body_size = 0
                for chunk in response.iter_bytes():
                    body_size += len(chunk)
                    if body_size > settings.feed_max_bytes:
                        raise FeedProbeError("Feed response exceeds configured size limit")
                    body_chunks.append(chunk)
                body = b"".join(body_chunks)
    except (httpx.HTTPError, SafeFetchError, RedirectError) as exc:
        raise FeedProbeError(f"Unable to fetch feed: {exc}") from exc

    parsed = feedparser.parse(body)
    metadata = parsed.feed if hasattr(parsed, "feed") else {}

    title = _clean(metadata.get("title"))
    description = _clean(metadata.get("subtitle") or metadata.get("description"))
    site_url = _clean(metadata.get("link"))
    language = _clean(metadata.get("language"))

    return FeedProbeResult(
        name=title,
        description=description,
        site_url=site_url,
        language=language,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        resolved_url=str(response.url),
        feed_type=_clean(getattr(parsed, "version", None)),
    )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
