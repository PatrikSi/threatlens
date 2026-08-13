from __future__ import annotations

import ast
import json

from app.services.ai_config import ActiveAISettings


def score_to_label(score: float | None, active: ActiveAISettings) -> str | None:
    if score is None:
        return None
    if score >= active.relevance_high_threshold:
        return "high"
    if score >= active.relevance_medium_threshold:
        return "medium"
    return "low"


def coerce_score(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        numeric = 0.0
    if numeric > 1:
        numeric = 1.0
    return round(numeric, 3)


def coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    source = value if isinstance(value, list) else [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in source:
        text = normalize_list_entry_text(entry)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def normalize_list_entry_text(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        extracted = extract_text_from_structured_list_entry(text)
        return extracted or text
    if isinstance(value, dict):
        for key in ("text", "content", "action", "summary", "message", "label", "title", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return str(value).strip()


def extract_text_from_structured_list_entry(value: str) -> str | None:
    if not value.startswith("{") or not value.endswith("}"):
        return None

    parsed: object | None = None
    try:
        parsed = json.loads(value)
    except ValueError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return None

    if isinstance(parsed, dict):
        return normalize_list_entry_text(parsed)
    return None


def truncate_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split()).strip()
    if not compact:
        return None
    return compact[:limit]
