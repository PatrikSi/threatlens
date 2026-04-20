from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

from app.services.connectors.base import Connector, NormalizedItem


class RSSFeedParseError(ValueError):
    pass


class RSSConnector(Connector):
    name = "rss"

    def poll(self, source_config: dict[str, Any], cursor: dict[str, Any] | None) -> tuple[list[NormalizedItem], dict[str, Any] | None]:
        _ = cursor
        body = source_config.get("body", b"")
        parsed = feedparser.parse(body)
        _require_feed_document(parsed)
        out: list[NormalizedItem] = []

        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("guid")
            url = entry.get("link")
            title = (entry.get("title") or "(untitled)").strip()
            summary = entry.get("summary") or entry.get("description")
            published_at = _parse_datetime(entry)
            out.append(
                NormalizedItem(
                    guid=guid,
                    url=url,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    raw=dict(entry) if hasattr(entry, "keys") else None,
                )
            )

        return out, None

    def supports_fulltext(self) -> bool:
        return True

    def fetch_fulltext(self, item: NormalizedItem):
        _ = item
        return None


def _require_feed_document(parsed: Any) -> None:
    if str(getattr(parsed, "version", "") or "").strip():
        return
    if getattr(parsed, "entries", None):
        return
    raise RSSFeedParseError("invalid_feed_content")


def _parse_datetime(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is not None:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    date_value = entry.get("published") or entry.get("updated")
    if date_value:
        try:
            dt = parsedate_to_datetime(date_value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None
