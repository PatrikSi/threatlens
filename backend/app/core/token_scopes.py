from collections.abc import Iterable

SCOPE_READ_FEEDS = "read:feeds"
SCOPE_WRITE_FEEDS = "write:feeds"
SCOPE_READ_ITEMS = "read:items"
SCOPE_WRITE_ITEMS = "write:items"
SCOPE_READ_TAGS = "read:tags"
SCOPE_WRITE_TAGS = "write:tags"
SCOPE_READ_VIEWS = "read:views"
SCOPE_WRITE_VIEWS = "write:views"
SCOPE_READ_ALERTS = "read:alerts"
SCOPE_WRITE_ALERTS = "write:alerts"
SCOPE_READ_TOKENS = "read:tokens"
SCOPE_WRITE_TOKENS = "write:tokens"
SCOPE_READ_NOTIFICATIONS = "read:notifications"
SCOPE_WRITE_NOTIFICATIONS = "write:notifications"
SCOPE_READ_USERS = "read:users"
SCOPE_WRITE_USERS = "write:users"
SCOPE_READ_AUDIT = "read:audit"
SCOPE_READ_STATS = "read:stats"

SCOPE_READ_ALL = "read:*"
SCOPE_WRITE_ALL = "write:*"
SCOPE_ADMIN_ALL = "admin:*"
SCOPE_ANY_ALL = "*:*"

ALLOWED_API_TOKEN_SCOPES = {
    SCOPE_READ_FEEDS,
    SCOPE_WRITE_FEEDS,
    SCOPE_READ_ITEMS,
    SCOPE_WRITE_ITEMS,
    SCOPE_READ_TAGS,
    SCOPE_WRITE_TAGS,
    SCOPE_READ_VIEWS,
    SCOPE_WRITE_VIEWS,
    SCOPE_READ_ALERTS,
    SCOPE_WRITE_ALERTS,
    SCOPE_READ_TOKENS,
    SCOPE_WRITE_TOKENS,
    SCOPE_READ_NOTIFICATIONS,
    SCOPE_WRITE_NOTIFICATIONS,
    SCOPE_READ_USERS,
    SCOPE_WRITE_USERS,
    SCOPE_READ_AUDIT,
    SCOPE_READ_STATS,
    SCOPE_READ_ALL,
    SCOPE_WRITE_ALL,
    SCOPE_ADMIN_ALL,
    SCOPE_ANY_ALL,
}

DEFAULT_API_TOKEN_SCOPES = (
    SCOPE_READ_FEEDS,
    SCOPE_READ_ITEMS,
    SCOPE_READ_STATS,
    SCOPE_READ_ALERTS,
)


def normalize_token_scopes(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return []
    normalized = {scope.strip().lower() for scope in scopes if scope and scope.strip()}
    return sorted(normalized)


def is_scope_allowed(scope: str) -> bool:
    return scope in ALLOWED_API_TOKEN_SCOPES


def has_required_scope(granted_scopes: set[str], required_scope: str) -> bool:
    if not required_scope:
        return True

    if SCOPE_ADMIN_ALL in granted_scopes or SCOPE_ANY_ALL in granted_scopes:
        return True

    if required_scope in granted_scopes:
        return True

    action, separator, resource = required_scope.partition(":")
    if not separator:
        return False

    wildcard_scope = f"{action}:*"
    if wildcard_scope in granted_scopes:
        return True

    # Write permission implies read for the same resource.
    if action == "read" and f"write:{resource}" in granted_scopes:
        return True

    return False
