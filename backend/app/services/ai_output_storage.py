"""Bound provider output before it can participate in durable settlement.

Content is rejected rather than rewritten so quotations keep their meaning.
Optional diagnostics may be omitted or escaped without changing feature output.
"""
from __future__ import annotations

import json
import math

MAX_OUTPUT_DEPTH = 32
MAX_OUTPUT_NODES = 100_000


class AIOutputStorageError(ValueError):
    pass


def _invalid_unicode(value: str) -> bool:
    return "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def optional_storage_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:limit]
    return None if _invalid_unicode(text) else text


def diagnostic_storage_text(value: str, *, limit: int = 1000) -> str:
    """Escape unsupported codepoints in bounded operator-facing diagnostics."""
    return value[:limit].replace("\x00", "\\u0000").encode("utf-8", errors="backslashreplace").decode("utf-8")[:limit]


def validate_output_storage(payload: object, *, max_bytes: int) -> None:
    """Validate all fields, including unused fields retained in stage artifacts."""
    stack = [(payload, 0)]
    seen = 0
    size = 0
    while stack:
        value, depth = stack.pop()
        seen += 1
        if seen + len(stack) > MAX_OUTPUT_NODES or depth > MAX_OUTPUT_DEPTH:
            raise AIOutputStorageError("JSON within the supported nesting and value-count limits")
        if isinstance(value, str):
            if len(value) > max_bytes:
                raise AIOutputStorageError("JSON within the configured response byte limit")
            size += 2  # Quotation marks; escape only small chunks at a time.
            for offset in range(0, len(value), 4096):
                chunk = value[offset:offset + 4096]
                if _invalid_unicode(chunk):
                    raise AIOutputStorageError("Unicode without U+0000 or unpaired surrogates")
                size += len(json.dumps(chunk, ensure_ascii=True)) - 2
                if size > max_bytes:
                    raise AIOutputStorageError("JSON within the configured response byte limit")
        elif isinstance(value, dict):
            size += 2 + max(0, len(value) - 1) + len(value)  # Braces, commas, colons.
            if len(value) * 2 + seen + len(stack) > MAX_OUTPUT_NODES:
                raise AIOutputStorageError("JSON within the supported value-count limit")
            for key, child in value.items():
                if not isinstance(key, str):
                    raise AIOutputStorageError("JSON object keys containing text")
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif isinstance(value, list):
            size += 2 + max(0, len(value) - 1)
            if len(value) + seen + len(stack) > MAX_OUTPUT_NODES:
                raise AIOutputStorageError("JSON within the supported value-count limit")
            stack.extend((child, depth + 1) for child in value)
        else:
            if isinstance(value, float) and not math.isfinite(value):
                raise AIOutputStorageError("JSON containing finite numbers")
            if value is not None and not isinstance(value, (bool, int, float)):
                raise AIOutputStorageError("supported JSON values")
            try:
                size += len(json.dumps(value, allow_nan=False))
            except ValueError as error:
                raise AIOutputStorageError("JSON within the supported serialization limits") from error
        if size > max_bytes:
            raise AIOutputStorageError("JSON within the configured response byte limit")
