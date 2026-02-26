import hashlib
from datetime import datetime
from urllib.parse import urlsplit

from app.services.url_utils import normalize_url


def content_hash(title: str, summary: str | None, url: str | None) -> str:
    payload = f"{title.strip()}|{(summary or '').strip()}|{(url or '').strip()}".lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dedupe_key(
    feed_id: str,
    source_guid: str | None,
    url: str | None,
    title: str,
    published_at: datetime | None,
) -> str:
    if source_guid:
        return f"guid:{feed_id}:{source_guid.strip()}"

    normalized_url = normalize_url(url)
    if normalized_url:
        return f"url:{normalized_url}"

    date_bucket = published_at.date().isoformat() if published_at else "unknown"
    domain = urlsplit(url or "").hostname or "unknown"
    raw = f"{title.strip().lower()}|{date_bucket}|{domain.lower()}"
    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"hash:{hashed}"
