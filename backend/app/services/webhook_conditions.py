"""Evaluate allowlisted conditions against an immutable event; unknown is never true."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.webhook_automation import (
    WebhookCondition,
    WebhookConditionGroup,
    WebhookConditionCheck,
)


def event_condition_values(
    payload: dict,
    *,
    created_at: datetime,
    now: datetime | None = None,
    event_type: str | None = None,
) -> dict:
    indicators = payload.get("indicators", [])
    indicators = (
        [
            entry
            for entry in indicators
            if isinstance(entry, dict) and not entry.get("excluded")
        ]
        if isinstance(indicators, list)
        else []
    )
    metadata = payload.get("filter_metadata") or {}
    tags = payload.get("tags") or []
    hunt = payload.get("hunt") or {}
    current = now or datetime.now(timezone.utc)
    created = (
        created_at.replace(tzinfo=timezone.utc)
        if created_at.tzinfo is None
        else created_at
    )
    freshness = max(0, (current - created).total_seconds())
    if event_type in {
        "intel.extraction.ready",
        "intel.indicators.changed",
        "hunt.approved",
    }:
        try:
            retrieved = datetime.fromisoformat(payload["article_retrieved_at"])
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
            age = (current - retrieved).total_seconds()
            freshness = age if age >= 0 else None
        except (KeyError, ValueError, TypeError):
            freshness = None
    values = {
        "feed_id": payload.get("feed_id"),
        "team_id": payload.get("team_id"),
        "tag_id": payload.get("tag_ids")
        or [tag.get("id") for tag in tags if isinstance(tag, dict)],
        "tag": [tag.get("name") if isinstance(tag, dict) else tag for tag in tags],
        "alert_rule_id": payload.get("alert_rule_ids"),
        "ioc_type": [entry.get("type") for entry in indicators],
        "ioc_role": [entry.get("role") for entry in indicators],
        "attack_technique": payload.get("attack_techniques"),
        "hunt_review_status": hunt.get("review_status"),
        "freshness_seconds": freshness,
    }
    for field in ("extraction_confidence", "maliciousness_confidence"):
        scores = [entry.get(field) for entry in indicators]
        # Missing confidence for even one selected indicator must not qualify the set.
        values[field] = (
            min(scores)
            if scores
            and all(
                isinstance(score, (int, float)) and not isinstance(score, bool)
                for score in scores
            )
            else None
        )
    for key in values:
        if key in metadata and key != "freshness_seconds":
            values[key] = metadata[key]
    aliases = {
        "tag": "tags",
        "tag_id": "tag_ids",
        "alert_rule_id": "alert_rule_ids",
        "attack_technique": "attack_techniques",
    }
    for field, key in aliases.items():
        if key in metadata:
            values[field] = metadata[key]
    if metadata.get("tags_complete") is False:
        values["tag"] = values["tag_id"] = None
    if metadata.get("alert_rules_complete") is False:
        values["alert_rule_id"] = None
    return values


def evaluate_conditions(
    condition: WebhookConditionGroup | None, values: dict
) -> tuple[bool, list[WebhookConditionCheck], list[str]]:
    checks: list[WebhookConditionCheck] = []
    missing: set[str] = set()

    def evaluate(node: WebhookCondition | WebhookConditionGroup) -> bool | None:
        if isinstance(node, WebhookConditionGroup):
            outcomes = [evaluate(child) for child in node.conditions]
            if node.op == "not":
                return None if outcomes[0] is None else not outcomes[0]
            if node.op == "all":
                return (
                    False if False in outcomes else (None if None in outcomes else True)
                )
            return True if True in outcomes else (None if None in outcomes else False)
        actual = values.get(node.field)
        if actual is None or actual == [] or actual == "":
            missing.add(node.field)
            checks.append(
                WebhookConditionCheck(
                    field=node.field,
                    matched=False,
                    reason="Field is unavailable in this event",
                )
            )
            return None
        if node.operator in {"gte", "lte"}:
            matched = isinstance(actual, (float, int)) and not isinstance(actual, bool)
            if matched:
                matched = (
                    actual >= node.value
                    if node.operator == "gte"
                    else actual <= node.value
                )
        else:
            entries = actual if isinstance(actual, list) else [actual]
            overlap = bool(
                {str(value).casefold() for value in entries if value is not None}
                & {value.casefold() for value in node.value}
            )
            matched = overlap if node.operator == "in" else not overlap
        checks.append(
            WebhookConditionCheck(
                field=node.field,
                matched=matched,
                reason="Condition matched" if matched else "Condition did not match",
            )
        )
        return matched

    matched = True if condition is None else evaluate(condition) is True
    return matched, checks, sorted(missing)
