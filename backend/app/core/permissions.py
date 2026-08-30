from __future__ import annotations

import uuid
from dataclasses import dataclass


SYSTEM_ROLE_IDS = {
    "admin": uuid.UUID("00000000-0000-4000-8000-000000000001"),
    "analyst": uuid.UUID("00000000-0000-4000-8000-000000000002"),
    "viewer": uuid.UUID("00000000-0000-4000-8000-000000000003"),
}
ALL_USERS_GROUP_ID = uuid.UUID("00000000-0000-4000-8000-000000000101")


@dataclass(frozen=True)
class PermissionDefinition:
    id: str
    group: str
    label: str
    description: str
    risk: str = "standard"
    delegable: bool = True


def _permission(
    permission_id: str,
    group: str,
    label: str,
    description: str,
    *,
    risk: str = "standard",
    delegable: bool = True,
) -> PermissionDefinition:
    return PermissionDefinition(
        id=permission_id,
        group=group,
        label=label,
        description=description,
        risk=risk,
        delegable=delegable,
    )


PERMISSION_DEFINITIONS: tuple[PermissionDefinition, ...] = (
    _permission(
        "read:feeds",
        "Intelligence",
        "View feeds",
        "View feed configuration and health.",
    ),
    _permission(
        "write:feeds",
        "Intelligence",
        "Manage feeds",
        "Create, update, fetch, and remove feeds.",
        risk="elevated",
    ),
    _permission(
        "admin:feeds",
        "Intelligence",
        "Administer feeds",
        "Delete feeds and export feed URLs for disaster recovery.",
        risk="critical",
    ),
    _permission(
        "read:items",
        "Intelligence",
        "View intelligence",
        "View collected articles and extracted intelligence.",
    ),
    _permission(
        "write:items",
        "Intelligence",
        "Triage intelligence",
        "Change article state, notes, and tags.",
    ),
    _permission(
        "read:tags", "Intelligence", "View tags", "View tags and tagging results."
    ),
    _permission(
        "write:tags",
        "Intelligence",
        "Manage tags",
        "Create tags and manage article tag assignments.",
    ),
    _permission(
        "read:tagging",
        "Intelligence",
        "View tagging policy",
        "View automated tagging settings, rules, and previews.",
        risk="elevated",
    ),
    _permission(
        "write:tagging",
        "Intelligence",
        "Manage tagging policy",
        "Change automated tagging rules and queue bulk reapplication.",
        risk="critical",
    ),
    _permission(
        "read:views",
        "Workspace",
        "View saved views",
        "View personal saved searches and layouts.",
    ),
    _permission(
        "write:views",
        "Workspace",
        "Manage saved views",
        "Create and change personal saved searches and layouts.",
    ),
    _permission(
        "read:alerts",
        "Detection",
        "View alerts",
        "View alert rules, occurrences, and activity.",
    ),
    _permission(
        "write:alerts",
        "Detection",
        "Manage alerts",
        "Create rules and triage alert occurrences.",
    ),
    _permission(
        "read:notifications",
        "Integrations",
        "View notifications",
        "View notification configuration and history.",
    ),
    _permission(
        "write:notifications",
        "Integrations",
        "Manage notifications",
        "Configure and test notification webhooks.",
    ),
    _permission(
        "read:integrations",
        "Integrations",
        "View integrations",
        "View integration instances and delivery history.",
    ),
    _permission(
        "write:integrations",
        "Integrations",
        "Manage integrations",
        "Configure integrations, credentials, and delivery retries.",
        risk="elevated",
    ),
    _permission(
        "read:reports",
        "Reporting",
        "View reports",
        "View generated reports and templates.",
    ),
    _permission(
        "write:reports",
        "Reporting",
        "Generate reports",
        "Create reports and manage owned templates.",
    ),
    _permission(
        "read:investigations",
        "Investigations",
        "View investigations",
        "View investigations allowed by object membership.",
    ),
    _permission(
        "write:investigations",
        "Investigations",
        "Manage investigations",
        "Create and update investigations allowed by object membership.",
    ),
    _permission(
        "read:stats",
        "Intelligence",
        "View statistics",
        "View intelligence and feed statistics.",
    ),
    _permission(
        "read:ai",
        "Administration",
        "View AI operations",
        "View AI configuration, usage, and operational history.",
    ),
    _permission(
        "write:ai",
        "Administration",
        "Manage AI",
        "Change AI configuration and queue administrative AI work.",
        risk="elevated",
    ),
    _permission(
        "read:health",
        "Operations",
        "View service health",
        "View worker, scheduler, and encryption health.",
    ),
    _permission(
        "read:operations",
        "Operations",
        "View operations",
        "View backup, recovery, and operational readiness.",
    ),
    _permission(
        "write:operations",
        "Operations",
        "Run operations",
        "Start registered operational actions.",
        risk="critical",
    ),
    _permission(
        "read:tokens",
        "Identity",
        "View personal tokens",
        "View the caller's personal API tokens.",
    ),
    _permission(
        "write:tokens",
        "Identity",
        "Manage personal tokens",
        "Create and revoke the caller's API tokens.",
        risk="elevated",
    ),
    _permission(
        "read:users",
        "Identity",
        "View users",
        "View the user directory and identity-provider configuration.",
        risk="elevated",
    ),
    _permission(
        "write:users",
        "Identity",
        "Manage users",
        "Create users and change account access or identity configuration.",
        risk="critical",
    ),
    _permission(
        "read:audit",
        "Governance",
        "View audit logs",
        "View and export administrative audit evidence.",
        risk="elevated",
    ),
    _permission(
        "read:iam",
        "Governance",
        "View access policy",
        "View roles, groups, assignments, and effective access.",
        risk="elevated",
    ),
    _permission(
        "write:iam",
        "Governance",
        "Manage access policy",
        "Create roles and change role or group assignments.",
        risk="critical",
    ),
    _permission(
        "read:workspace",
        "Workspace",
        "View workspace policy",
        "View effective module and workspace policy.",
    ),
    _permission(
        "write:workspace_preferences",
        "Workspace",
        "Customize personal workspace",
        "Change the caller's optional navigation and dashboard preferences.",
    ),
    _permission(
        "write:workspace",
        "Workspace",
        "Manage workspace policy",
        "Change organization workspace defaults and module policy.",
        risk="elevated",
    ),
    _permission(
        "read:service_accounts",
        "Identity",
        "View service accounts",
        "View non-human principals and their credentials.",
        risk="elevated",
    ),
    _permission(
        "write:service_accounts",
        "Identity",
        "Manage service accounts",
        "Create, disable, and rotate non-human credentials.",
        risk="critical",
    ),
    _permission(
        "read:elevations",
        "Governance",
        "View elevations",
        "View temporary access requests and grants.",
        risk="elevated",
    ),
    _permission(
        "write:elevations",
        "Governance",
        "Request elevations",
        "Request or revoke temporary role grants.",
        risk="elevated",
    ),
    _permission(
        "approve:elevations",
        "Governance",
        "Approve elevations",
        "Approve another person's temporary role grant.",
        risk="critical",
    ),
    _permission(
        "read:approvals",
        "Governance",
        "View approvals",
        "View approval policies, requests, and decisions.",
        risk="elevated",
    ),
    _permission(
        "write:approvals",
        "Governance",
        "Request approvals",
        "Request approval for a registered sensitive action.",
        risk="elevated",
    ),
    _permission(
        "approve:approvals",
        "Governance",
        "Approve actions",
        "Approve another principal's sensitive action.",
        risk="critical",
    ),
    _permission(
        "read:access_reviews",
        "Governance",
        "View access reviews",
        "View access-review campaigns and decisions.",
        risk="elevated",
    ),
    _permission(
        "write:access_reviews",
        "Governance",
        "Manage access reviews",
        "Create campaigns and record or apply review decisions.",
        risk="critical",
    ),
    _permission(
        "read:data_policies",
        "Governance",
        "View data policies",
        "View handling labels and feed access policies.",
        risk="elevated",
    ),
    _permission(
        "write:data_policies",
        "Governance",
        "Manage data policies",
        "Change handling labels and activate feed restrictions.",
        risk="critical",
    ),
)

PERMISSION_BY_ID = {permission.id: permission for permission in PERMISSION_DEFINITIONS}
ALL_PERMISSION_IDS = frozenset(PERMISSION_BY_ID)
NON_DELEGABLE_PERMISSION_IDS = frozenset(
    permission.id for permission in PERMISSION_DEFINITIONS if not permission.delegable
)

WILDCARD_PERMISSION_IDS = frozenset({"read:*", "write:*", "admin:*", "*:*"})
# Wildcards would make a custom role silently inherit permissions introduced by a
# future release. Custom roles therefore enumerate concrete permissions only.
RESERVED_CUSTOM_ROLE_PERMISSION_IDS = WILDCARD_PERMISSION_IDS


def is_known_permission(permission_id: str) -> bool:
    return (
        permission_id in ALL_PERMISSION_IDS or permission_id in WILDCARD_PERMISSION_IDS
    )


def expand_permission_grants(grants: set[str] | frozenset[str]) -> frozenset[str]:
    from app.core.token_scopes import has_required_scope

    return frozenset(
        permission_id
        for permission_id in ALL_PERMISSION_IDS
        if has_required_scope(set(grants), permission_id)
    )


__all__ = [
    "ALL_USERS_GROUP_ID",
    "ALL_PERMISSION_IDS",
    "NON_DELEGABLE_PERMISSION_IDS",
    "PERMISSION_BY_ID",
    "PERMISSION_DEFINITIONS",
    "RESERVED_CUSTOM_ROLE_PERMISSION_IDS",
    "SYSTEM_ROLE_IDS",
    "PermissionDefinition",
    "expand_permission_grants",
    "is_known_permission",
]
