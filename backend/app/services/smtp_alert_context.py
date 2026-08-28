from __future__ import annotations

import uuid

from app.services.notification_webhook_templates import AlertMatchContext

SMTP_ALERT_NAME_CAP = 100
SMTP_ALERT_KEYWORD_CAP = 512
SMTP_LEGACY_ALERT_OCCURRENCE_CAP = 500


def combine_smtp_alert_contexts(
    contexts: dict[uuid.UUID, AlertMatchContext],
) -> AlertMatchContext:
    ordered = [contexts[owner_id] for owner_id in sorted(contexts, key=str)]
    names: list[str] = []
    categories: list[str] = []
    keywords: list[str] = []
    for context in ordered:
        remaining = SMTP_ALERT_NAME_CAP - len(names)
        if remaining > 0:
            names.extend(context.names[:remaining])
            categories.extend(context.categories[:remaining])
        for keyword in context.matched_keywords:
            if keyword not in keywords and len(keywords) < SMTP_ALERT_KEYWORD_CAP:
                keywords.append(keyword)
    return AlertMatchContext(
        count=sum(context.count for context in ordered),
        primary_name=ordered[0].primary_name,
        names=names,
        categories=categories,
        matched_keywords=keywords,
    )


def legacy_smtp_payload_alert_context(payload: dict) -> AlertMatchContext:
    snapshot = payload.get("alert")
    if isinstance(snapshot, dict):
        primary_name = snapshot.get("primary_name")
        if isinstance(primary_name, str) and primary_name.strip():
            names = snapshot.get("names")
            categories = snapshot.get("categories")
            keywords = snapshot.get("matched_keywords")
            count_value = snapshot.get("count")
            count = (
                min(count_value, SMTP_LEGACY_ALERT_OCCURRENCE_CAP)
                if type(count_value) is int and count_value > 0
                else 1
            )
            return AlertMatchContext(
                count=count,
                primary_name=primary_name.strip()[:255],
                names=[
                    value[:255]
                    for value in names[:SMTP_ALERT_NAME_CAP]
                    if isinstance(value, str)
                ]
                if isinstance(names, list)
                else [primary_name.strip()[:255]],
                categories=[
                    value[:64]
                    for value in categories[:SMTP_ALERT_NAME_CAP]
                    if isinstance(value, str)
                ]
                if isinstance(categories, list)
                else [],
                matched_keywords=[
                    value[:255]
                    for value in keywords[:SMTP_ALERT_KEYWORD_CAP]
                    if isinstance(value, str)
                ]
                if isinstance(keywords, list)
                else [],
            )
    return AlertMatchContext(
        count=1,
        primary_name="Legacy alert match",
        names=["Legacy alert match"],
        categories=[],
        matched_keywords=[],
    )
