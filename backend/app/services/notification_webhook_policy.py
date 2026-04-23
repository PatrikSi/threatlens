from __future__ import annotations

from dataclasses import dataclass
from posixpath import normpath
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
class _NotificationTarget:
    scheme: str
    host: str
    port: int | None
    path: str

    @property
    def origin(self) -> _NotificationOrigin:
        return _NotificationOrigin(scheme=self.scheme, host=self.host, port=self.port)

    @property
    def canonical_origin(self) -> str:
        return self.origin.canonical

    @property
    def canonical_target(self) -> str:
        if self.path == "/":
            return self.canonical_origin
        return f"{self.canonical_origin}{self.path}"


@dataclass(frozen=True)
class _NotificationAllowEntry:
    scheme: str
    host: str
    port: int | None
    wildcard: bool
    path_prefix: str | None

    @property
    def canonical(self) -> str:
        origin = _NotificationOrigin(
            scheme=self.scheme,
            host=f"*.{self.host}" if self.wildcard else self.host,
            port=self.port,
        ).canonical
        if self.path_prefix is None:
            return origin
        return f"{origin}{self.path_prefix}"


def normalize_notification_allow_entry(allow_entry: str) -> str:
    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        raise ValueError(
            "notification_webhook_allowed_hosts entries must be hostnames, host:port pairs, or http(s) URL prefixes without credentials, query strings, or fragments"
        )

    candidate = str(allow_entry).strip()
    if "://" not in candidate and "/" not in candidate and parsed_allow_entry.scheme == "https" and parsed_allow_entry.port == 443:
        return f"*.{parsed_allow_entry.host}" if parsed_allow_entry.wildcard else parsed_allow_entry.host
    return parsed_allow_entry.canonical


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

    target = _parse_notification_target(url)
    if any(notification_target_matches_allowlist(target, entry) for entry in allowed_hosts):
        return
    raise ValueError(
        f"Webhook destination '{target.canonical_target}' is not approved for analyst-managed webhook deliveries"
    )


def notification_target_host(url: str) -> str:
    return _parse_notification_target(url).host


def notification_host_matches_allowlist(host: str, allow_entry: str) -> bool:
    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        return False
    if parsed_allow_entry.wildcard:
        return host.endswith(f".{parsed_allow_entry.host}")
    return host == parsed_allow_entry.host


def notification_target_origin(url: str) -> str:
    return _parse_notification_target(url).canonical_origin


def notification_target_matches_allowlist(target: _NotificationTarget | str, allow_entry: str) -> bool:
    parsed_target = _parse_notification_target(target) if isinstance(target, str) else target
    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        return False
    if parsed_target.scheme != parsed_allow_entry.scheme or parsed_target.port != parsed_allow_entry.port:
        return False
    if parsed_allow_entry.wildcard:
        host_matches = parsed_target.host.endswith(f".{parsed_allow_entry.host}")
    else:
        host_matches = parsed_target.host == parsed_allow_entry.host
    if not host_matches:
        return False
    return _path_matches_allow_prefix(parsed_target.path, parsed_allow_entry.path_prefix)


def notification_origin_matches_allowlist(origin: _NotificationOrigin | str, allow_entry: str) -> bool:
    if isinstance(origin, str):
        return notification_target_matches_allowlist(origin, allow_entry)

    parsed_allow_entry = _parse_notification_allow_entry(allow_entry)
    if parsed_allow_entry is None:
        return False
    if parsed_allow_entry.path_prefix is not None:
        return False

    target_origin = origin
    if target_origin.scheme != parsed_allow_entry.scheme or target_origin.port != parsed_allow_entry.port:
        return False
    if parsed_allow_entry.wildcard:
        return target_origin.host.endswith(f".{parsed_allow_entry.host}")
    return target_origin.host == parsed_allow_entry.host


def _parse_notification_target(url: _NotificationTarget | str) -> _NotificationTarget:
    if isinstance(url, _NotificationTarget):
        return url

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

    return _NotificationTarget(
        scheme=scheme,
        host=host,
        port=port,
        path=_normalize_notification_path(split.path),
    )


def _parse_notification_origin(url: str) -> _NotificationOrigin:
    return _parse_notification_target(url).origin


def _parse_notification_allow_entry(allow_entry: str) -> _NotificationAllowEntry | None:
    candidate = str(allow_entry).strip()
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

    path_prefix = None
    if split.path not in {"", "/"}:
        path_prefix = _normalize_notification_path(split.path)

    return _NotificationAllowEntry(
        scheme=scheme,
        host=normalized_host,
        port=port,
        wildcard=wildcard,
        path_prefix=path_prefix,
    )


def _normalize_notification_path(path: str | None) -> str:
    raw_path = path or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"

    normalized = normpath(raw_path)
    if normalized in {"", "."}:
        return "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _path_matches_allow_prefix(target_path: str, path_prefix: str | None) -> bool:
    if path_prefix is None:
        return True
    if target_path == path_prefix:
        return True
    return target_path.startswith(f"{path_prefix}/")


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None
