from collections.abc import Iterable

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER

SCOPE_READ_FEEDS = "read:feeds"
SCOPE_WRITE_FEEDS = "write:feeds"
SCOPE_ADMIN_FEEDS = "admin:feeds"
SCOPE_READ_ITEMS = "read:items"
SCOPE_WRITE_ITEMS = "write:items"
SCOPE_READ_TAGS = "read:tags"
SCOPE_WRITE_TAGS = "write:tags"
SCOPE_READ_TAGGING = "read:tagging"
SCOPE_WRITE_TAGGING = "write:tagging"
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
SCOPE_READ_AI = "read:ai"
SCOPE_WRITE_AI = "write:ai"
SCOPE_READ_INTEGRATIONS = "read:integrations"
SCOPE_WRITE_INTEGRATIONS = "write:integrations"
SCOPE_READ_REPORTS = "read:reports"
SCOPE_WRITE_REPORTS = "write:reports"
SCOPE_READ_HEALTH = "read:health"
SCOPE_READ_OPERATIONS = "read:operations"
SCOPE_WRITE_OPERATIONS = "write:operations"
SCOPE_READ_INVESTIGATIONS = "read:investigations"
SCOPE_WRITE_INVESTIGATIONS = "write:investigations"
SCOPE_READ_IAM = "read:iam"
SCOPE_WRITE_IAM = "write:iam"
SCOPE_READ_WORKSPACE = "read:workspace"
SCOPE_WRITE_WORKSPACE_PREFERENCES = "write:workspace_preferences"
SCOPE_WRITE_WORKSPACE = "write:workspace"
SCOPE_READ_SERVICE_ACCOUNTS = "read:service_accounts"
SCOPE_WRITE_SERVICE_ACCOUNTS = "write:service_accounts"
SCOPE_READ_ELEVATIONS = "read:elevations"
SCOPE_WRITE_ELEVATIONS = "write:elevations"
SCOPE_APPROVE_ELEVATIONS = "approve:elevations"
SCOPE_READ_APPROVALS = "read:approvals"
SCOPE_WRITE_APPROVALS = "write:approvals"
SCOPE_APPROVE_APPROVALS = "approve:approvals"
SCOPE_READ_ACCESS_REVIEWS = "read:access_reviews"
SCOPE_WRITE_ACCESS_REVIEWS = "write:access_reviews"
SCOPE_READ_DATA_POLICIES = "read:data_policies"
SCOPE_WRITE_DATA_POLICIES = "write:data_policies"

SCOPE_READ_ALL = "read:*"
SCOPE_WRITE_ALL = "write:*"
SCOPE_ADMIN_ALL = "admin:*"
SCOPE_ANY_ALL = "*:*"

ALLOWED_API_TOKEN_SCOPES = {
    SCOPE_READ_FEEDS,
    SCOPE_WRITE_FEEDS,
    SCOPE_ADMIN_FEEDS,
    SCOPE_READ_ITEMS,
    SCOPE_WRITE_ITEMS,
    SCOPE_READ_TAGS,
    SCOPE_WRITE_TAGS,
    SCOPE_READ_TAGGING,
    SCOPE_WRITE_TAGGING,
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
    SCOPE_READ_AI,
    SCOPE_WRITE_AI,
    SCOPE_READ_INTEGRATIONS,
    SCOPE_WRITE_INTEGRATIONS,
    SCOPE_READ_REPORTS,
    SCOPE_WRITE_REPORTS,
    SCOPE_READ_HEALTH,
    SCOPE_READ_OPERATIONS,
    SCOPE_WRITE_OPERATIONS,
    SCOPE_READ_INVESTIGATIONS,
    SCOPE_WRITE_INVESTIGATIONS,
    SCOPE_READ_IAM,
    SCOPE_WRITE_IAM,
    SCOPE_READ_WORKSPACE,
    SCOPE_WRITE_WORKSPACE_PREFERENCES,
    SCOPE_WRITE_WORKSPACE,
    SCOPE_READ_SERVICE_ACCOUNTS,
    SCOPE_WRITE_SERVICE_ACCOUNTS,
    SCOPE_READ_ELEVATIONS,
    SCOPE_WRITE_ELEVATIONS,
    SCOPE_APPROVE_ELEVATIONS,
    SCOPE_READ_APPROVALS,
    SCOPE_WRITE_APPROVALS,
    SCOPE_APPROVE_APPROVALS,
    SCOPE_READ_ACCESS_REVIEWS,
    SCOPE_WRITE_ACCESS_REVIEWS,
    SCOPE_READ_DATA_POLICIES,
    SCOPE_WRITE_DATA_POLICIES,
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

ROLE_API_TOKEN_SCOPE_GRANTS = {
    ROLE_ADMIN: frozenset({SCOPE_ANY_ALL}),
    ROLE_ANALYST: frozenset(
        {
            SCOPE_READ_FEEDS,
            SCOPE_READ_ITEMS,
            SCOPE_READ_TAGS,
            SCOPE_READ_NOTIFICATIONS,
            SCOPE_READ_STATS,
            SCOPE_WRITE_ALERTS,
            SCOPE_WRITE_VIEWS,
            SCOPE_WRITE_TOKENS,
            SCOPE_WRITE_FEEDS,
            SCOPE_WRITE_ITEMS,
            SCOPE_WRITE_TAGS,
            SCOPE_WRITE_NOTIFICATIONS,
            SCOPE_READ_WORKSPACE,
            SCOPE_WRITE_WORKSPACE_PREFERENCES,
            SCOPE_READ_REPORTS,
            SCOPE_WRITE_REPORTS,
            SCOPE_READ_INVESTIGATIONS,
            SCOPE_WRITE_INVESTIGATIONS,
        }
    ),
    ROLE_VIEWER: frozenset(
        {
            SCOPE_READ_FEEDS,
            SCOPE_READ_ITEMS,
            SCOPE_READ_TAGS,
            SCOPE_READ_NOTIFICATIONS,
            SCOPE_READ_STATS,
            SCOPE_WRITE_ALERTS,
            SCOPE_WRITE_VIEWS,
            SCOPE_WRITE_TOKENS,
            SCOPE_READ_WORKSPACE,
            SCOPE_WRITE_WORKSPACE_PREFERENCES,
            SCOPE_READ_REPORTS,
            SCOPE_READ_INVESTIGATIONS,
        }
    ),
}


def normalize_token_scopes(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return []
    normalized = {scope.strip().lower() for scope in scopes if scope and scope.strip()}
    return sorted(normalized)


def is_scope_allowed(scope: str) -> bool:
    return scope in ALLOWED_API_TOKEN_SCOPES


def get_role_api_token_scope_grants(role: str) -> frozenset[str]:
    return ROLE_API_TOKEN_SCOPE_GRANTS.get(role, frozenset())


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
    if action == "read" and (f"write:{resource}" in granted_scopes or SCOPE_WRITE_ALL in granted_scopes):
        return True

    return False


def missing_delegable_scopes(granted_scopes: Iterable[str], requested_scopes: Iterable[str]) -> list[str]:
    granted = set(normalize_token_scopes(granted_scopes))
    requested = normalize_token_scopes(requested_scopes)
    return [scope for scope in requested if not has_required_scope(granted, scope)]


def missing_role_token_scopes(role: str, requested_scopes: Iterable[str]) -> list[str]:
    granted = set(get_role_api_token_scope_grants(role))
    requested = normalize_token_scopes(requested_scopes)
    return [scope for scope in requested if not has_required_scope(granted, scope)]
