from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

from app.models.user import User


def validate_notification_target_for_actor(
    url: str,
    *,
    actor_user: User | SimpleNamespace | None,
    allowed_hosts: tuple[str, ...],
) -> None:
    if actor_user is None:
        raise ValueError("Webhook owner is no longer active and approved for outbound delivery")

    if not getattr(actor_user, "is_active", True) or not getattr(actor_user, "is_approved", True):
        raise ValueError("Webhook owner is no longer active and approved for outbound delivery")

    if getattr(actor_user, "role", None) == "admin":
        return

    if not allowed_hosts:
        raise ValueError(
            "Analyst-managed webhook deliveries are disabled until NOTIFICATION_WEBHOOK_ALLOWED_HOSTS is configured"
        )

    target_host = notification_target_host(url)
    if any(notification_host_matches_allowlist(target_host, entry) for entry in allowed_hosts):
        return
    raise ValueError(
        f"Webhook destination host '{target_host}' is not approved for analyst-managed webhook deliveries"
    )


def notification_target_host(url: str) -> str:
    try:
        split = urlsplit(url)
    except ValueError as exc:
        raise ValueError("Webhook destination URL is invalid") from exc

    host = (split.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Webhook destination URL must include a hostname")
    return host


def notification_host_matches_allowlist(host: str, allow_entry: str) -> bool:
    if allow_entry.startswith("*."):
        suffix = allow_entry[2:]
        return host == suffix or host.endswith(f".{suffix}")
    return host == allow_entry
