"""Stable failure attribution; operational metrics never parse English messages."""
from __future__ import annotations

import httpx

from app.services.outbound_deadline import OutboundDeadlineExceeded, OutboundDNSDeadlineExceeded

DEADLINE_CATEGORIES = frozenset({"total_deadline", "dns_deadline"})
TIMEOUT_CATEGORIES = DEADLINE_CATEGORIES | {"connect_timeout", "read_timeout", "write_timeout", "pool_timeout", "provider_timeout"}


def transport_failure_category(error: BaseException) -> str:
    current: BaseException | None = error
    seen: set[int] = set()
    categories: list[str] = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OutboundDNSDeadlineExceeded):
            return "dns_deadline"
        if isinstance(current, OutboundDeadlineExceeded) or getattr(current, "_threatlens_deadline_recorded", False):
            categories.append("total_deadline")
        for kind, category in (
            (httpx.ConnectTimeout, "connect_timeout"), (httpx.ReadTimeout, "read_timeout"),
            (httpx.WriteTimeout, "write_timeout"), (httpx.PoolTimeout, "pool_timeout"),
        ):
            if isinstance(current, kind):
                categories.append(category)
        current = current.__cause__ or current.__context__
    return "total_deadline" if "total_deadline" in categories else categories[0] if categories else "transport"


def http_failure_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "provider_auth"
    if status_code == 429:
        return "provider_rate_limit"
    if status_code == 408:
        return "provider_timeout"
    return "provider_server" if status_code >= 500 else "provider_request"


def provider_usage_identity(active) -> dict:
    return {
        "provider_id": getattr(active, "provider_id", None),
        "provider_version": getattr(active, "provider_version", None),
        "provider_name": getattr(active, "provider_name", None) or (
            "Legacy settings" if getattr(active, "provider_id", None) is None else "Unnamed provider"
        ),
    }
