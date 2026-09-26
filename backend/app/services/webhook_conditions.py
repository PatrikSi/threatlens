"""Evaluate allowlisted conditions against an immutable event; unknown is never true."""

from __future__ import annotations

from datetime import datetime, timezone
import math

from app.schemas.webhook_automation import (
    WebhookCondition,
    WebhookConditionGroup,
    WebhookConditionCheck,
    INDICATOR_OPERATORS,
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
    metadata = payload.get("filter_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    tags = payload.get("tags")
    tags = tags if isinstance(tags, list) else []
    hunt = payload.get("hunt")
    hunt = hunt if isinstance(hunt, dict) else {}
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
        "analyst_verdict": [entry.get("analyst_verdict") for entry in indicators],
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
    # Internal evidence cannot be supplied through filter metadata. Keep excluded
    # entries for preview, but never allow them to satisfy an indicator predicate.
    snapshot = payload.get("indicators")
    values["_indicators"] = (
        snapshot if isinstance(snapshot, list) and len(snapshot) <= 250
        and payload.get("indicators_complete") is not False
        and all(isinstance(entry, dict) and type(entry.get("excluded")) is bool for entry in snapshot)
        else None
    )
    return values


def evaluate_conditions(
    condition: WebhookConditionGroup | None, values: dict
) -> tuple[bool, list[WebhookConditionCheck], list[str]]:
    checks: list[WebhookConditionCheck] = []
    missing: set[str] = set()

    def combine(op: str, outcomes: list[bool | None]) -> bool | None:
        if op == "not":
            return None if outcomes[0] is None else not outcomes[0]
        if op == "all":
            return False if False in outcomes else (None if None in outcomes else True)
        return True if True in outcomes else (None if None in outcomes else False)

    def evaluate(
        node: WebhookCondition | WebhookConditionGroup,
        scope: dict,
        path: str,
        *,
        report: bool = True,
    ) -> bool | None:
        if isinstance(node, WebhookConditionGroup):
            if node.op in INDICATOR_OPERATORS:
                return evaluate_indicators(node, path)
            return combine(node.op, [
                evaluate(child, scope, f"{path}.{index}", report=report)
                for index, child in enumerate(node.conditions)
            ])
        actual = scope.get(node.field)
        if not _condition_value_available(node.field, actual):
            missing.add(node.field)
            if report:
                checks.append(WebhookConditionCheck(
                    field=node.field, matched=False,
                    reason="Field is unavailable in this event", condition_path=path,
                ))
            return None
        if node.operator in {"gte", "lte"}:
            matched = actual >= node.value if node.operator == "gte" else actual <= node.value
        else:
            entries = actual if isinstance(actual, list) else [actual]
            overlap = bool(
                {str(value).casefold() for value in entries if value is not None}
                & {value.casefold() for value in node.value}
            )
            matched = overlap if node.operator == "in" else not overlap
        if report:
            checks.append(WebhookConditionCheck(
                field=node.field, matched=matched,
                reason="Condition matched" if matched else "Condition did not match",
                condition_path=path,
            ))
        return matched

    def evaluate_indicators(node: WebhookConditionGroup, path: str) -> bool | None:
        snapshot = values.get("_indicators")
        if snapshot is None:
            missing.add("indicators")
            checks.append(WebhookConditionCheck(
                field=node.op, matched=False, condition_path=path,
                reason="A complete indicator inventory with exclusion status is unavailable",
            ))
            return None
        outcomes: list[bool | None] = []
        omitted = 0
        for entry in snapshot:
            excluded = entry["excluded"]
            scope = {
                "ioc_type": entry.get("type"), "ioc_role": entry.get("role"),
                "analyst_verdict": entry.get("analyst_verdict"),
                "extraction_confidence": entry.get("extraction_confidence"),
                "maliciousness_confidence": entry.get("maliciousness_confidence"),
            }
            outcome = False if excluded else combine("all", [
                evaluate(child, scope, f"{path}.{index}", report=False)
                for index, child in enumerate(node.conditions)
            ])
            if not excluded:
                outcomes.append(outcome)
            if len(checks) < 250:
                checks.append(WebhookConditionCheck(
                    field=node.op, matched=outcome is True, condition_path=path,
                    indicator_id=_preview_text(entry.get("id"), 80),
                    indicator_type=_preview_text(entry.get("type"), 50),
                    indicator_value=_preview_text(entry.get("value"), 200),
                    indicator_excluded=excluded,
                    reason=("Excluded indicator is not eligible" if excluded else
                            "Indicator evidence is unavailable" if outcome is None else
                            "This indicator matched" if outcome else "This indicator did not match"),
                ))
            else:
                omitted += 1
        # Do not use vacuous truth: an empty eligible inventory must not trigger a hunt.
        outcome = combine("all" if node.op == "indicators_all" else "any", outcomes) if outcomes else False
        checks.append(WebhookConditionCheck(
            field=node.op, matched=outcome is True, condition_path=path,
            reason=f"Evaluated {len(outcomes)} eligible indicators; {omitted} preview rows omitted",
        ))
        return outcome

    matched = True if condition is None else evaluate(condition, values, "0") is True
    return matched, checks, sorted(missing)


def _condition_value_available(field: str, value: object) -> bool:
    """Malformed or incomplete values remain unknown, including under NOT."""
    if field in {
        "extraction_confidence",
        "maliciousness_confidence",
        "freshness_seconds",
    }:
        return (
            isinstance(value, (float, int))
            and not isinstance(value, bool)
            and (isinstance(value, int) or math.isfinite(value))
            and value >= 0
            and (field == "freshness_seconds" or value <= 1)
        )
    values = value if isinstance(value, list) else [value]
    return bool(values) and all(
        isinstance(entry, str)
        and bool(entry)
        and "\x00" not in entry
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in entry)
        for entry in values
    )


def _preview_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value if len(value) <= limit else value[:limit] + "…"
