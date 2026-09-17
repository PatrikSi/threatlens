"""Shared bounded HTTP correlation IDs for application and transport errors."""

import uuid

_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")


def normalize_request_id(raw_request_id: str | None) -> str:
    candidate = "".join(char for char in (raw_request_id or "").strip() if char in _ALLOWED)
    return candidate[:128] if candidate else str(uuid.uuid4())
