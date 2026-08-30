from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.token_scopes import (
    SCOPE_READ_AI,
    SCOPE_READ_ALERTS,
    SCOPE_READ_AUDIT,
    SCOPE_READ_FEEDS,
    SCOPE_READ_INTEGRATIONS,
    SCOPE_READ_INVESTIGATIONS,
    SCOPE_READ_ITEMS,
    SCOPE_READ_NOTIFICATIONS,
    SCOPE_READ_OPERATIONS,
    SCOPE_READ_REPORTS,
    SCOPE_READ_STATS,
    SCOPE_READ_TAGGING,
    SCOPE_READ_USERS,
    SCOPE_WRITE_TOKENS,
)
from app.schemas.workspace import (
    WorkspaceDashboardPanelDefinitionResponse,
    WorkspaceModuleDefinitionResponse,
    WorkspaceRegistryResponse,
    WorkspaceRole,
)


WORKSPACE_ROLES: tuple[WorkspaceRole, ...] = ("admin", "analyst", "viewer")
_ALL_ROLES = frozenset(WORKSPACE_ROLES)
_ADMIN_ONLY = frozenset({"admin"})


@dataclass(frozen=True)
class WorkspaceModuleDefinition:
    id: str
    label: str
    route: str
    section: Literal["primary", "settings"]
    parent_id: str | None
    required_permissions: tuple[str, ...]
    feature_flag: str | None
    default_visible_roles: frozenset[str]
    default_optional: bool
    default_order: int
    default_mobile_priority: int
    mobile_behavior: Literal["primary", "secondary"]

    @property
    def required_permission(self) -> str | None:
        """Compatibility projection for clients built before all-of requirements."""
        return self.required_permissions[0] if self.required_permissions else None


@dataclass(frozen=True)
class WorkspaceDashboardPanelDefinition:
    id: str
    label: str
    required_permissions: tuple[str, ...]
    feature_flag: str | None = None

    @property
    def required_permission(self) -> str | None:
        return self.required_permissions[0] if self.required_permissions else None


def _module(
    module_id: str,
    label: str,
    route: str,
    *,
    order: int,
    permissions: tuple[str, ...] = (),
    feature_flag: str | None = None,
    roles: frozenset[str] = _ALL_ROLES,
    optional: bool = True,
    parent_id: str | None = None,
    mobile_behavior: Literal["primary", "secondary"] = "secondary",
) -> WorkspaceModuleDefinition:
    section: Literal["primary", "settings"] = (
        "settings" if module_id.startswith("settings.") else "primary"
    )
    if section == "settings" and parent_id is None:
        parent_id = "primary.settings"
    return WorkspaceModuleDefinition(
        id=module_id,
        label=label,
        route=route,
        section=section,
        parent_id=parent_id,
        required_permissions=permissions,
        feature_flag=feature_flag,
        default_visible_roles=roles,
        default_optional=optional,
        default_order=order,
        default_mobile_priority=order,
        mobile_behavior=mobile_behavior,
    )


WORKSPACE_MODULES: tuple[WorkspaceModuleDefinition, ...] = (
    _module(
        "primary.dashboard",
        "Dashboard",
        "/",
        order=0,
        permissions=(SCOPE_READ_ITEMS,),
        optional=False,
        mobile_behavior="primary",
    ),
    _module(
        "primary.alerts",
        "Alerts",
        "/alerts",
        order=10,
        permissions=(SCOPE_READ_ALERTS, SCOPE_READ_ITEMS),
        mobile_behavior="primary",
    ),
    _module(
        "primary.investigations",
        "Investigations",
        "/investigations",
        order=20,
        permissions=(SCOPE_READ_INVESTIGATIONS,),
        mobile_behavior="primary",
    ),
    _module(
        "primary.feeds",
        "Feeds",
        "/feeds",
        order=30,
        permissions=(SCOPE_READ_FEEDS,),
        mobile_behavior="primary",
    ),
    _module(
        "primary.stats",
        "Stats",
        "/stats",
        order=40,
        permissions=(SCOPE_READ_STATS,),
    ),
    _module(
        "primary.export",
        "Export",
        "/export",
        order=50,
        permissions=(SCOPE_READ_ITEMS,),
    ),
    _module(
        "primary.reporting",
        "Reporting",
        "/reporting",
        order=60,
        permissions=(SCOPE_READ_REPORTS,),
    ),
    _module("primary.settings", "Settings", "/settings", order=70, optional=False),
    _module(
        "settings.account",
        "Account",
        "/settings/account",
        order=0,
        optional=False,
        mobile_behavior="primary",
    ),
    _module(
        "settings.tokens",
        "API Tokens",
        "/settings/tokens",
        order=10,
        permissions=(SCOPE_WRITE_TOKENS,),
        mobile_behavior="primary",
    ),
    _module(
        "settings.ai",
        "AI",
        "/settings/ai",
        order=20,
        permissions=(SCOPE_READ_AI,),
        feature_flag="ai_enabled",
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.tagging",
        "Tagging",
        "/settings/tagging",
        order=30,
        permissions=(SCOPE_READ_TAGGING,),
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.identity",
        "Identity",
        "/settings/identity",
        order=40,
        permissions=(SCOPE_READ_USERS,),
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.users",
        "Users",
        "/settings/users",
        order=50,
        permissions=(SCOPE_READ_USERS,),
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.audit",
        "Audit Logs",
        "/settings/audit-logs",
        order=60,
        permissions=(SCOPE_READ_AUDIT,),
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.operations",
        "Operations",
        "/settings/operations",
        order=70,
        permissions=(SCOPE_READ_OPERATIONS,),
        roles=_ADMIN_ONLY,
    ),
    _module(
        "settings.integrations",
        "Integrations",
        "/settings/integrations",
        order=80,
        permissions=(SCOPE_READ_NOTIFICATIONS,),
    ),
    _module(
        "settings.integrations.webhooks",
        "Webhooks",
        "/settings/integrations/webhooks",
        order=90,
        permissions=(SCOPE_READ_NOTIFICATIONS,),
        parent_id="settings.integrations",
    ),
    _module(
        "settings.integrations.smtp",
        "SMTP",
        "/settings/integrations/smtp",
        order=100,
        permissions=(SCOPE_READ_INTEGRATIONS,),
        roles=_ADMIN_ONLY,
        parent_id="settings.integrations",
    ),
)

WORKSPACE_DASHBOARD_PANELS: tuple[WorkspaceDashboardPanelDefinition, ...] = (
    WorkspaceDashboardPanelDefinition("rss", "RSS intelligence", (SCOPE_READ_ITEMS,)),
    WorkspaceDashboardPanelDefinition(
        "alerts",
        "Alerts",
        (SCOPE_READ_ALERTS, SCOPE_READ_ITEMS),
    ),
    WorkspaceDashboardPanelDefinition("notes", "Notes", ()),
    WorkspaceDashboardPanelDefinition(
        "daily_brief",
        "AI daily brief",
        (SCOPE_READ_ITEMS,),
        "ai_daily_brief_enabled",
    ),
)

WORKSPACE_MODULE_BY_ID = {module.id: module for module in WORKSPACE_MODULES}
WORKSPACE_DASHBOARD_PANEL_BY_ID = {
    panel.id: panel for panel in WORKSPACE_DASHBOARD_PANELS
}


def workspace_registry_response() -> WorkspaceRegistryResponse:
    return WorkspaceRegistryResponse(
        modules=[
            WorkspaceModuleDefinitionResponse(
                id=module.id,
                label=module.label,
                route=module.route,
                section=module.section,
                parent_id=module.parent_id,
                required_permission=module.required_permission,
                required_permissions=list(module.required_permissions),
                feature_flag=module.feature_flag,
                default_optional=module.default_optional,
                default_order=module.default_order,
                default_mobile_priority=module.default_mobile_priority,
                mobile_behavior=module.mobile_behavior,
            )
            for module in WORKSPACE_MODULES
        ],
        dashboard_panels=[
            WorkspaceDashboardPanelDefinitionResponse(
                id=panel.id,
                label=panel.label,
                required_permission=panel.required_permission,
                required_permissions=list(panel.required_permissions),
                feature_flag=panel.feature_flag,
            )
            for panel in WORKSPACE_DASHBOARD_PANELS
        ],
    )


def default_role_modules(role: WorkspaceRole) -> dict[str, dict[str, object]]:
    return {
        module.id: {
            "visible": role in module.default_visible_roles,
            "optional": module.default_optional,
            "order": module.default_order,
            "mobile_priority": module.default_mobile_priority,
        }
        for module in WORKSPACE_MODULES
    }


def runtime_workspace_feature_flags(db: Session | None = None) -> dict[str, bool]:
    settings = get_settings()
    flags = {
        "ai_enabled": bool(settings.ai_enabled),
        "ai_daily_brief_enabled": bool(settings.ai_enabled),
    }
    if db is not None and settings.ai_enabled:
        from app.services.ai_config import load_public_ai_feature_flags

        ai_flags = load_public_ai_feature_flags(db)
        flags["ai_daily_brief_enabled"] = ai_flags.ai_daily_brief_enabled
    return flags


__all__ = [
    "WORKSPACE_DASHBOARD_PANEL_BY_ID",
    "WORKSPACE_DASHBOARD_PANELS",
    "WORKSPACE_MODULE_BY_ID",
    "WORKSPACE_MODULES",
    "WORKSPACE_ROLES",
    "WorkspaceDashboardPanelDefinition",
    "WorkspaceModuleDefinition",
    "default_role_modules",
    "runtime_workspace_feature_flags",
    "workspace_registry_response",
]
