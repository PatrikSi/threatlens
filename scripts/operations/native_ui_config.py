"""Nonsecret configuration for the unchanged native UI review driver."""
from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import urlsplit

_STATIC_CONFIG = {'axeTags': ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'],
 'routes': ['/',
            '/alerts',
            '/investigations',
            '/teams',
            '/feeds',
            '/stats',
            '/export',
            '/reporting',
            '/settings/account',
            '/settings/tokens',
            '/settings/workspace',
            '/settings/ai',
            '/settings/tagging',
            '/settings/identity',
            '/settings/access',
            '/settings/users',
            '/settings/audit-logs',
            '/settings/lifecycle',
            '/settings/operations',
            '/settings/integrations/webhooks',
            '/settings/integrations/smtp'],
 'mobileRoutes': ['/',
                  '/investigations',
                  '/teams',
                  '/export',
                  '/settings/account',
                  '/settings/ai'],
 'desktop': {'width': 1440, 'height': 1000},
 'mobile': {'width': 390, 'height': 844},
 'themes': ['light', 'dark'],
 'intentionalRedirects': {'/settings': '/settings/account',
                          '/settings/integrations': '/settings/integrations/webhooks',
                          '/settings/notifications': '/settings/integrations/webhooks',
                          '/ai': '/settings/ai'},
 'version': '2.1.0',
 'defaultTimeoutMs': 30000,
 'engines': ['chromium', 'firefox', 'webkit']}


def _origin(value: str, hosts: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError("Native UI origin must be a string")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
    ):
        raise ValueError("Native UI origin must be owned HTTP with an explicit port")
    return value.rstrip("/")


def build_config(
    base_url: str,
    publisher_origin: str,
    source_revision: str,
    actual_images: dict[str, str],
) -> dict:
    """Keep all original scopes and budgets; inject only fresh owned provenance."""
    base_url = _origin(base_url, {"127.0.0.1", "localhost"})
    publisher_origin = _origin(publisher_origin, {"review-source"})
    if not isinstance(source_revision, str) or re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise ValueError("Native UI source must be a full commit revision")
    if not isinstance(actual_images, dict) or set(actual_images) != {"backend", "web"}:
        raise ValueError("Both actual production image identities are required")
    if any(not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in actual_images.values()):
        raise ValueError("Native UI image identities must be immutable SHA256 values")
    config = deepcopy(_STATIC_CONFIG)
    config.update({
        "baseURL": base_url,
        "ownedFixtureSourceOrigin": publisher_origin,
        "sourceSha": source_revision,
        "actualImages": dict(actual_images),
    })
    return config
