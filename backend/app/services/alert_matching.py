from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import String, cast, func, or_

from app.models.item import Item
from app.models.item_classification import ItemClassification


def normalize_alert_keywords(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        keyword = " ".join(str(raw).strip().lower().split())
        if not keyword or keyword in seen:
            continue
        normalized.append(keyword)
        seen.add(keyword)
    return normalized


def build_alert_keyword_condition(keyword: str):
    pattern = f"%{escape_like(keyword)}%"
    return or_(
        func.lower(Item.title).like(pattern, escape="\\"),
        func.lower(func.coalesce(Item.summary, "")).like(pattern, escape="\\"),
        func.lower(cast(Item.url, String)).like(pattern, escape="\\"),
        func.lower(cast(func.coalesce(Item.canonical_url, ""), String)).like(
            pattern, escape="\\"
        ),
        func.lower(func.coalesce(ItemClassification.primary_category, "")).like(
            pattern, escape="\\"
        ),
    )


def build_item_haystack(
    *,
    title: str,
    summary: str | None,
    url: str,
    canonical_url: str | None,
    classification: str | None,
) -> str:
    return " ".join(
        [title, summary or "", url, canonical_url or "", classification or ""]
    ).lower()


def match_alert_keywords(keywords: Iterable[str], haystack: str) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword in haystack]


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
