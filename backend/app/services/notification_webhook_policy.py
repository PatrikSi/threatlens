from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit

from app.models.user import User


@dataclass(frozen=True)
class _NotificationOrigin:
    scheme: str
    host: str
    port: int | None

    @property
    def canonical(self) -> str:
        default_port = _default_port_for_scheme(self.scheme)
        if self.port is None or self.port == default_port:
            return f"{self.scheme}://{self.host}"
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class _NotificationAllowEntry:
    scheme: str
    host: str
    port: int | None
    wildcard: bool


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

    target_origin = _parse_notification_origin(url)
    if any(notification_origin_matches_allowlist(target_origin, entry) for entry in allowed_hosts):
        return
    raise ValueError(
        f"Webhook destination origin '{target_origin.canonical}' is not approved for analyst-managed webhook deliveries"
    )


def notification_target_host(url: str) -> str:
    return _parse_notification_origin(url).host


def notification_host_matches_allowlist(host: str, allow_entry: str) -> bool:
    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        return False
    if parsed_allow_entry.wildcard:
        return host.endswith(f".{parsed_allow_entry.host}")
    return host == parsed_allow_entry.host


def notification_target_origin(url: str) -> str:
    return _parse_notification_origin(url).canonical


def notification_origin_matches_allowlist(origin: _NotificationOrigin | str, allow_entry: str) -> bool:
    target_origin = _parse_notification_origin(origin) if isinstance(origin, str) else origin
    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        return False
    if target_origin.scheme != parsed_allow_entry.scheme or target_origin.port != parsed_allow_entry.port:
        return False
    if parsed_allow_entry.wildcard:
        return target_origin.host.endswith(f".{parsed_allow_entry.host}")
    return target_origin.host == parsed_allow_entry.host


def _parse_notification_origin(url: str) -> _NotificationOrigin:
    try:
        split = urlsplit(url)
        port = split.port
    except ValueError as exc:
        raise ValueError("Webhook destination URL is invalid") from exc

    scheme = split.scheme.strip().lower()
    host = (split.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Webhook destination URL must include a hostname")

    if port is None:
        port = _default_port_for_scheme(scheme)

    return _NotificationOrigin(scheme=scheme, host=host, port=port)


def _parse_notification_allow_entry(allow_entry: str) -> _NotificationAllowEntry | None:
    candidate = str(allow_entry).strip().lower().rstrip(".")
    if not candidate:
        return None

    raw_entry = candidate if "://" in candidate else f"https://{candidate}"

    try:
        split = urlsplit(raw_entry)
        port = split.port
    except ValueError:
        return None

    if split.username or split.password or split.query or split.fragment:
        return None
    if split.path not in {"", "/"}:
        return None

    scheme = split.scheme.strip().lower()
    host = (split.hostname or "").strip().lower().rstrip(".")
    if not host:
        return None

    wildcard = host.startswith("*.")
    normalized_host = host[2:] if wildcard else host
    if not normalized_host:
        return None

    if port is None:
        port = _default_port_for_scheme(scheme)

    return _NotificationAllowEntry(scheme=scheme, host=normalized_host, port=port, wildcard=wildcard)


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None
