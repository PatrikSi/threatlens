from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


WorkspaceRole = Literal["admin", "analyst", "viewer"]
WorkspaceSection = Literal["primary", "settings"]
WorkspaceMobileBehavior = Literal["primary", "secondary"]


class StrictWorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceModuleDefinitionResponse(StrictWorkspaceModel):
    id: str
    label: str
    route: str
    section: WorkspaceSection
    parent_id: str | None = None
    required_permission: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    feature_flag: str | None = None
    default_optional: bool
    default_order: int = Field(ge=0)
    default_mobile_priority: int = Field(ge=0)
    mobile_behavior: WorkspaceMobileBehavior
    policy_managed: bool


class WorkspaceDashboardPanelDefinitionResponse(StrictWorkspaceModel):
    id: str
    label: str
    required_permission: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    feature_flag: str | None = None


class WorkspaceRegistryResponse(StrictWorkspaceModel):
    modules: list[WorkspaceModuleDefinitionResponse]
    dashboard_panels: list[WorkspaceDashboardPanelDefinitionResponse]


class WorkspaceModulePolicy(StrictWorkspaceModel):
    module_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_.-]+$")
    visible: bool
    optional: bool
    order: int = Field(ge=0, le=10_000)
    mobile_priority: int = Field(ge=0, le=10_000)

    @field_validator("module_id", mode="before")
    @classmethod
    def strip_module_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class WorkspaceRolePolicyWriteRequest(StrictWorkspaceModel):
    expected_revision: int = Field(ge=1)
    landing_module_id: str = Field(
        min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_.-]+$"
    )
    modules: list[WorkspaceModulePolicy] = Field(min_length=1, max_length=128)
    dashboard_panel_ids: list[str] = Field(max_length=32)

    @field_validator("landing_module_id", mode="before")
    @classmethod
    def strip_landing_module_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("dashboard_panel_ids", mode="before")
    @classmethod
    def normalize_dashboard_panel_ids(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [entry.strip() if isinstance(entry, str) else entry for entry in value]

    @model_validator(mode="after")
    def reject_duplicate_ids(self):
        module_ids = [module.module_id for module in self.modules]
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("modules must not contain duplicate module IDs")
        if len(self.dashboard_panel_ids) != len(set(self.dashboard_panel_ids)):
            raise ValueError("dashboard_panel_ids must not contain duplicates")
        return self


class WorkspaceRolePolicyResetRequest(StrictWorkspaceModel):
    expected_revision: int = Field(ge=1)


class WorkspaceRolePolicyResponse(StrictWorkspaceModel):
    role: WorkspaceRole
    landing_module_id: str
    modules: list[WorkspaceModulePolicy]
    dashboard_panel_ids: list[str]
    revision: int = Field(ge=1)
    updated_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    unknown_module_ids: list[str] = Field(default_factory=list)
    unknown_dashboard_panel_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkspaceModulePreference(StrictWorkspaceModel):
    module_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_.-]+$")
    visible: bool | None = None
    order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("module_id", mode="before")
    @classmethod
    def strip_module_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_preference(self):
        if self.visible is None and self.order is None:
            raise ValueError("a module preference must set visible or order")
        return self


class WorkspaceUserPreferenceWriteRequest(StrictWorkspaceModel):
    expected_revision: int = Field(ge=0)
    landing_module_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.-]+$",
    )
    modules: list[WorkspaceModulePreference] = Field(
        default_factory=list, max_length=128
    )
    dashboard_panel_ids: list[str] | None = Field(default=None, max_length=32)

    @field_validator("landing_module_id", mode="before")
    @classmethod
    def strip_landing_module_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("dashboard_panel_ids", mode="before")
    @classmethod
    def normalize_dashboard_panel_ids(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [entry.strip() if isinstance(entry, str) else entry for entry in value]

    @model_validator(mode="after")
    def reject_duplicate_ids(self):
        module_ids = [module.module_id for module in self.modules]
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("modules must not contain duplicate module IDs")
        if self.dashboard_panel_ids is not None and len(
            self.dashboard_panel_ids
        ) != len(set(self.dashboard_panel_ids)):
            raise ValueError("dashboard_panel_ids must not contain duplicates")
        return self


class WorkspaceUserPreferenceResetRequest(StrictWorkspaceModel):
    expected_revision: int = Field(ge=0)


class WorkspaceUserPreferenceResponse(StrictWorkspaceModel):
    user_id: uuid.UUID
    role: WorkspaceRole
    landing_module_id: str | None
    modules: list[WorkspaceModulePreference]
    dashboard_panel_ids: list[str] | None
    revision: int = Field(ge=0)
    updated_by_user_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    unknown_module_ids: list[str] = Field(default_factory=list)
    unknown_dashboard_panel_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkspaceEffectiveModuleResponse(StrictWorkspaceModel):
    id: str
    label: str
    route: str
    section: WorkspaceSection
    parent_id: str | None
    visible: bool
    optional: bool
    order: int = Field(ge=0)
    mobile_priority: int = Field(ge=0)
    mobile_behavior: WorkspaceMobileBehavior
    permission_allowed: bool
    missing_permissions: list[str] = Field(default_factory=list)
    feature_available: bool
    policy_visible: bool
    preference_visible: bool
    reasons: list[str]


class WorkspaceEffectiveDashboardPanelResponse(StrictWorkspaceModel):
    id: str
    visible: bool
    permission_allowed: bool
    feature_available: bool
    missing_permissions: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class WorkspaceEffectiveResponse(StrictWorkspaceModel):
    role: WorkspaceRole
    policy_revision: int = Field(ge=1)
    preference_revision: int = Field(ge=0)
    landing_module_id: str | None
    dashboard_panel_ids: list[str]
    dashboard_panels: list[WorkspaceEffectiveDashboardPanelResponse] = Field(
        default_factory=list
    )
    modules: list[WorkspaceEffectiveModuleResponse]
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "WorkspaceDashboardPanelDefinitionResponse",
    "WorkspaceEffectiveDashboardPanelResponse",
    "WorkspaceEffectiveModuleResponse",
    "WorkspaceEffectiveResponse",
    "WorkspaceMobileBehavior",
    "WorkspaceModuleDefinitionResponse",
    "WorkspaceModulePolicy",
    "WorkspaceModulePreference",
    "WorkspaceRegistryResponse",
    "WorkspaceRole",
    "WorkspaceRolePolicyResponse",
    "WorkspaceRolePolicyResetRequest",
    "WorkspaceRolePolicyWriteRequest",
    "WorkspaceSection",
    "WorkspaceUserPreferenceResponse",
    "WorkspaceUserPreferenceResetRequest",
    "WorkspaceUserPreferenceWriteRequest",
]
