from __future__ import annotations

from typing import Protocol

import feedparser


class FeedMetadataTarget(Protocol):
    name: str
    url: str
    description: str | None
    site_url: str | None
    language: str | None
    etag: str | None
    last_modified: str | None


class ProbeMetadata(Protocol):
    name: str | None
    description: str | None
    site_url: str | None
    language: str | None
    etag: str | None
    last_modified: str | None


def needs_metadata_backfill(feed: FeedMetadataTarget) -> bool:
    placeholder_name = not feed.name.strip() or feed.name.strip() == feed.url.strip()
    return placeholder_name or not feed.site_url


def apply_probe_metadata(feed: FeedMetadataTarget, metadata: ProbeMetadata) -> bool:
    changed = False
    is_placeholder_name = not feed.name.strip() or feed.name.strip() == feed.url.strip()

    if is_placeholder_name and metadata.name:
        feed.name = metadata.name
        changed = True
    if not feed.description and metadata.description:
        feed.description = metadata.description
        changed = True
    if not feed.site_url and metadata.site_url:
        feed.site_url = metadata.site_url
        changed = True
    if not feed.language and metadata.language:
        feed.language = metadata.language
        changed = True
    if not feed.etag and metadata.etag:
        feed.etag = metadata.etag
        changed = True
    if not feed.last_modified and metadata.last_modified:
        feed.last_modified = metadata.last_modified
        changed = True

    return changed


def backfill_feed_metadata_from_body(feed: FeedMetadataTarget, body: bytes) -> bool:
    parsed = feedparser.parse(body)
    metadata = parsed.feed if hasattr(parsed, "feed") else {}

    changed = False
    feed_title = _clean_text(metadata.get("title"))
    description = _clean_text(metadata.get("subtitle") or metadata.get("description"))
    site_url = _clean_text(metadata.get("link"))
    language = _clean_text(metadata.get("language"))

    if (not feed.name.strip() or feed.name.strip() == feed.url.strip()) and feed_title:
        feed.name = feed_title
        changed = True
    if not feed.description and description:
        feed.description = description
        changed = True
    if not feed.site_url and site_url:
        feed.site_url = site_url
        changed = True
    if not feed.language and language:
        feed.language = language
        changed = True

    return changed


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
