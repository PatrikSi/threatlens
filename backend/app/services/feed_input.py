"""Validate publisher input before it reaches persistence or request headers."""

from __future__ import annotations

import re

import feedparser


class FeedInputError(ValueError):
    """The publisher document cannot be safely accepted as feed evidence."""


_UNSUPPORTED_TEXT = re.compile("[\x00\ud800-\udfff]")
_CACHE_HEADER = re.compile(r"[\t\x20-\x7e]+")
_METADATA_TEXT_FIELDS = ("title", "subtitle", "description", "link", "language")
_ENTRY_TEXT_FIELDS = ("id", "guid", "link", "title", "summary", "description")


def clean_feed_text(value: object, *, max_chars: int | None = None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _UNSUPPORTED_TEXT.search(value):
        # Reject evidence instead of silently changing quotations or identities.
        raise FeedInputError("invalid_feed_content")
    text = value.strip()
    if max_chars is not None and len(text) > max_chars:
        return None
    return text or None


def parse_feed_document(body: bytes) -> feedparser.FeedParserDict:
    try:
        parsed = feedparser.parse(body)
    except (ValueError, OverflowError) as exc:
        # feedparser's tolerant XML fallback can raise UnicodeEncodeError for
        # malformed numeric character references; it is a ValueError subclass.
        raise FeedInputError("invalid_feed_content") from exc
    if not str(getattr(parsed, "version", "") or "").strip() and not parsed.entries:
        raise FeedInputError("invalid_feed_content")
    for field in _METADATA_TEXT_FIELDS:
        clean_feed_text(parsed.feed.get(field))
    for entry in parsed.entries:
        for field in _ENTRY_TEXT_FIELDS:
            clean_feed_text(entry.get(field))
    return parsed


def safe_feed_cache_header(value: object) -> str | None:
    """Unsupported optional validators fall back to an unconditional fetch.

    HTTPX encodes string request headers as ASCII. Validate both new response
    values and historical stored values so one unusual ETag cannot stop polling.
    """
    if not isinstance(value, str) or len(value) > 8_192:
        return None
    return value if _CACHE_HEADER.fullmatch(value) else None
