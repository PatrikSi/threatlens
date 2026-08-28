from __future__ import annotations

import math
import re
import uuid
from datetime import datetime
from typing import Any

from app.core.logging_config import redact_log_text


MAX_METADATA_DEPTH = 4
MAX_METADATA_ENTRIES = 24
MAX_METADATA_NODES = 48
MAX_METADATA_STRING_CHARS = 256
MAX_ERROR_MESSAGE_CHARS = 1000
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")
_URL_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s,;]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9.])/(?!/)[^\s,;]+")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\[^\s,;]+")
_UNC_PATH_PATTERN = re.compile(r"\\\\[^\\\s,;]+\\[^\s,;]+")
_RELATIVE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(?:[A-Za-z0-9_.-]+[/\\])+(?:[A-Za-z0-9_.-]+)"
)
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_HOST_PORT_PATTERN = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}:[0-9]{2,5}\b")
_HOSTNAME_PATTERN = re.compile(
    r"(?i)\b(?:[a-z0-9-]+\.)+(?:[a-z]{2,63}|internal|local)\b"
)
_IP_ADDRESS_PATTERN = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]{2,5})?\b")
_SERVICE_VALUE_PATTERN = re.compile(
    r"(?i)\b((?:oidc|smtp)[a-z0-9_ -]{0,32}\s*[=:]\s*)[^\s,;]+"
)
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "backup_path",
        "client_id",
        "client_secret",
        "connection_string",
        "connection_url",
        "cookie",
        "credentials",
        "csrf",
        "database_url",
        "directory",
        "dsn",
        "file_path",
        "headers",
        "host",
        "hostname",
        "id_token",
        "encryption_key",
        "issuer_url",
        "oidc",
        "password",
        "path",
        "recipient",
        "redis_url",
        "refresh_token",
        "private_key",
        "secret",
        "smtp",
        "smtp_password",
        "token",
        "url",
        "username",
    }
)
_METADATA_KEY_SUFFIXES = (
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_dsn",
    "_key",
    "_password",
    "_path",
    "_secret",
    "_token",
    "_url",
)


def sanitize_operation_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"_redacted": "Stored metadata was not a structured object."}
    sanitized = _MetadataSanitizer().sanitize(value, depth=0)
    if not isinstance(sanitized, dict):
        return {"_redacted": "Stored metadata could not be represented safely."}
    return sanitized


class _MetadataSanitizer:
    def __init__(self) -> None:
        self.nodes_remaining = MAX_METADATA_NODES

    def sanitize(self, value: object, *, depth: int) -> Any:
        if self.nodes_remaining <= 0 or depth > MAX_METADATA_DEPTH:
            return "[TRUNCATED]"
        self.nodes_remaining -= 1

        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (datetime, uuid.UUID)):
            return str(value)
        if isinstance(value, str):
            return sanitize_text(value, max_chars=MAX_METADATA_STRING_CHARS)
        if isinstance(value, dict):
            return self._sanitize_dict(value, depth=depth)
        if isinstance(value, (list, tuple)):
            values = list(value)
            result = [self.sanitize(item, depth=depth + 1) for item in values[:MAX_METADATA_ENTRIES]]
            if len(values) > MAX_METADATA_ENTRIES:
                result.append("[TRUNCATED]")
            return result
        return "[REDACTED_UNSUPPORTED_VALUE]"

    def _sanitize_dict(self, value: dict, *, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        items = list(value.items())
        for index, (raw_key, raw_value) in enumerate(items[:MAX_METADATA_ENTRIES]):
            key = sanitize_metadata_key(raw_key, index=index)
            if is_sensitive_metadata_key(str(raw_key)):
                result[key] = "[REDACTED]"
            else:
                result[key] = self.sanitize(raw_value, depth=depth + 1)
        if len(items) > MAX_METADATA_ENTRIES:
            result["_truncated"] = True
        return result


def sanitize_text(value: object | None, *, max_chars: int) -> str | None:
    if value is None:
        return None
    sanitized = redact_log_text(value, max_chars=max_chars * 2)
    sanitized = _URL_PATTERN.sub("[REDACTED_URL]", sanitized)
    sanitized = _UNIX_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _UNC_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _RELATIVE_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = _HOST_PORT_PATTERN.sub("[REDACTED_HOST]", sanitized)
    sanitized = _HOSTNAME_PATTERN.sub("[REDACTED_HOST]", sanitized)
    sanitized = _IP_ADDRESS_PATTERN.sub("[REDACTED_HOST]", sanitized)
    sanitized = _SERVICE_VALUE_PATTERN.sub(r"\1[REDACTED]", sanitized)
    if len(sanitized) > max_chars:
        return f"{sanitized[:max_chars]}...[truncated]"
    return sanitized


def sanitize_identity(value: object | None) -> str:
    if value is None:
        return "system"
    sanitized = redact_log_text(value, max_chars=255)
    sanitized = _URL_PATTERN.sub("[REDACTED_URL]", sanitized)
    sanitized = _UNIX_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    sanitized = _WINDOWS_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    return sanitized.strip()[:255] or "system"


def sanitize_source(value: object | None) -> str:
    candidate = str(value or "offline").strip()[:64]
    return candidate if _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate) else "offline"


def sanitize_error_code(value: object | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()[:64]
    return candidate if candidate and _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate) else "operation_failed"


def sanitize_metadata_key(value: object, *, index: int) -> str:
    candidate = str(value).strip()[:64]
    if candidate and _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate):
        return candidate
    return f"field_{index + 1}"


def is_sensitive_metadata_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if normalized in {"encryption_key_fingerprint", "key_fingerprint", "token_count", "token_counts"}:
        return False
    return (
        normalized in _SENSITIVE_METADATA_KEYS
        or normalized.startswith(("oidc_", "smtp_"))
        or normalized.endswith(_METADATA_KEY_SUFFIXES)
    )


def safe_string_list(value: object, *, fallback: list[str]) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return list(fallback)
    result = []
    for item in value:
        candidate = str(item).strip()[:64]
        if candidate and _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate):
            result.append(candidate)
    return sorted(set(result))


def safe_reason(value: object) -> str:
    candidate = str(value or "unknown").strip()[:64]
    return candidate if _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate) else "unknown"


def safe_revision(value: object | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or len(candidate) > 64 or not _SAFE_IDENTIFIER_PATTERN.fullmatch(candidate):
        return None
    return candidate
