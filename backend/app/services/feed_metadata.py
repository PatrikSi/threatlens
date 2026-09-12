from __future__ import annotations

from typing import Protocol

from app.models.feed import Feed
from app.services.feed_input import clean_feed_text, parse_feed_document, safe_feed_cache_header


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
    if not feed.etag and (etag := safe_feed_cache_header(metadata.etag)):
        feed.etag = etag
        changed = True
    if not feed.last_modified and (last_modified := safe_feed_cache_header(metadata.last_modified)):
        feed.last_modified = last_modified
        changed = True

    return changed


def backfill_feed_metadata_from_body(feed: FeedMetadataTarget, body: bytes) -> bool:
    parsed = parse_feed_document(body)
    metadata = parsed.feed if hasattr(parsed, "feed") else {}

    changed = False
    feed_title = clean_feed_text(metadata.get("title"))
    description = clean_feed_text(metadata.get("subtitle") or metadata.get("description"))
    site_url = clean_feed_text(metadata.get("link"))
    language = clean_feed_text(metadata.get("language"), max_chars=64)

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


def needs_feed_metadata_backfill(feed: Feed) -> bool:
    if feed.url_decryption_error:
        return False
    return needs_metadata_backfill(feed)
