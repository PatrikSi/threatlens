from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.workspace import WorkspaceRolePolicy, WorkspaceUserPreference
from app.schemas.workspace import (
    WorkspaceEffectiveDashboardPanelResponse,
    WorkspaceEffectiveModuleResponse,
    WorkspaceEffectiveResponse,
    WorkspaceModulePolicy,
    WorkspaceModulePreference,
    WorkspaceRole,
    WorkspaceRolePolicyResponse,
    WorkspaceRolePolicyWriteRequest,
    WorkspaceUserPreferenceResponse,
    WorkspaceUserPreferenceWriteRequest,
)
from app.services.authorization import AuthorizationContext
from app.services.workspace_policy_registry import (
    WORKSPACE_DASHBOARD_PANEL_BY_ID,
    WORKSPACE_DASHBOARD_PANELS,
    WORKSPACE_MODULE_BY_ID,
    WORKSPACE_MODULES,
    WORKSPACE_ROLES,
    default_role_modules,
    runtime_workspace_feature_flags,
    workspace_registry_response,
)


_WORKSPACE_SNAPSHOT_ATTEMPTS = 3
_DEFAULT_LANDING_MODULE = "primary.dashboard"
_DEFAULT_DASHBOARD_PANELS = ["rss"]


class WorkspacePolicyError(RuntimeError):
    code = "workspace_policy_error"
    status_code = 409

    def __init__(
        self,
        detail: str,
        *,
        context: dict[str, object] | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context = context
        self.current_revision = current_revision


class WorkspaceRoleInvalid(WorkspacePolicyError):
    code = "workspace_role_invalid"
    status_code = 404


class WorkspacePolicyUnavailable(WorkspacePolicyError):
    code = "workspace_policy_unavailable"
    status_code = 503


class WorkspaceSnapshotUnavailable(WorkspacePolicyError):
    code = "workspace_snapshot_unavailable"
    status_code = 503


class WorkspacePolicyRevisionConflict(WorkspacePolicyError):
    code = "workspace_policy_revision_conflict"
    status_code = 409


class WorkspacePreferenceRevisionConflict(WorkspacePolicyError):
    code = "workspace_preference_revision_conflict"
    status_code = 409


class WorkspaceUnknownModule(WorkspacePolicyError):
    code = "workspace_unknown_module"
    status_code = 422


class WorkspaceUnknownDashboardPanel(WorkspacePolicyError):
    code = "workspace_unknown_dashboard_panel"
    status_code = 422


class WorkspacePolicyModuleSetIncomplete(WorkspacePolicyError):
    code = "workspace_policy_module_set_incomplete"
    status_code = 422


class WorkspaceModuleNotCustomizable(WorkspacePolicyError):
    code = "workspace_module_not_customizable"
    status_code = 409


class WorkspaceLandingModuleUnavailable(WorkspacePolicyError):
    code = "workspace_landing_module_unavailable"
    status_code = 422


def list_role_policies(db: Session) -> list[WorkspaceRolePolicyResponse]:
    rows = db.scalars(
        select(WorkspaceRolePolicy).order_by(WorkspaceRolePolicy.role)
    ).all()
    by_role = {row.role: row for row in rows}
    missing = [role for role in WORKSPACE_ROLES if role not in by_role]
    if missing:
        raise WorkspacePolicyUnavailable(
            "Workspace role defaults are incomplete. Restore the database or rerun migrations.",
            context={"missing_roles": missing},
        )
    return [role_policy_response(by_role[role]) for role in WORKSPACE_ROLES]


def get_role_policy(db: Session, role: str) -> WorkspaceRolePolicyResponse:
    normalized_role = _workspace_role(role)
    row = db.get(WorkspaceRolePolicy, normalized_role)
    if row is None:
        raise WorkspacePolicyUnavailable(
            "The workspace policy for this built-in role is missing. Restore the database or rerun migrations.",
            context={"role": normalized_role},
        )
    return role_policy_response(row)


def update_role_policy(
    db: Session,
    *,
    role: str,
    payload: WorkspaceRolePolicyWriteRequest,
    actor_user_id: uuid.UUID,
) -> WorkspaceRolePolicyResponse:
    normalized_role = _workspace_role(role)
    row = db.scalar(
        select(WorkspaceRolePolicy)
        .where(WorkspaceRolePolicy.role == normalized_role)
        .with_for_update()
    )
    if row is None:
        raise WorkspacePolicyUnavailable(
            "The workspace policy for this built-in role is missing. Restore the database or rerun migrations.",
            context={"role": normalized_role},
        )
    if row.revision != payload.expected_revision:
        raise WorkspacePolicyRevisionConflict(
            "The workspace role policy changed after it was loaded. Reload it and retry.",
            current_revision=row.revision,
            context={
                "role": normalized_role,
                "expected_revision": payload.expected_revision,
                "current_revision": row.revision,
            },
        )

    supplied_ids = {module.module_id for module in payload.modules}
    unknown_ids = sorted(supplied_ids - WORKSPACE_MODULE_BY_ID.keys())
    if unknown_ids:
        raise _unknown_module_error(unknown_ids)
    missing_ids = sorted(WORKSPACE_MODULE_BY_ID.keys() - supplied_ids)
    if missing_ids:
        raise WorkspacePolicyModuleSetIncomplete(
            "The complete trusted module set is required when replacing a role policy. Reload the policy and retry.",
            context={"missing_module_ids": missing_ids},
        )
    _validate_dashboard_panels(payload.dashboard_panel_ids)

    modules_by_id = {module.module_id: module for module in payload.modules}
    landing = modules_by_id.get(payload.landing_module_id)
    if landing is None:
        if (
            payload.landing_module_id != row.landing_module_id
            or payload.landing_module_id in WORKSPACE_MODULE_BY_ID
        ):
            raise _unknown_module_error([payload.landing_module_id])
    elif not _policy_landing_available(payload.landing_module_id, modules_by_id):
        raise WorkspaceLandingModuleUnavailable(
            "The role landing module and its parent navigation modules must remain visible in the organization policy.",
            context={"module_id": payload.landing_module_id},
        )

    unknown_stored_modules = _unknown_mapping_entries(row.modules_json)
    unknown_stored_panels = _unknown_string_entries(
        row.dashboard_panel_ids_json, WORKSPACE_DASHBOARD_PANEL_BY_ID
    )
    row.modules_json = {
        **unknown_stored_modules,
        **{
            module.module_id: {
                "visible": module.visible,
                "optional": module.optional,
                "order": module.order,
                "mobile_priority": module.mobile_priority,
            }
            for module in payload.modules
        },
    }
    row.landing_module_id = payload.landing_module_id
    row.dashboard_panel_ids_json = [
        *payload.dashboard_panel_ids,
        *unknown_stored_panels,
    ]
    row.revision += 1
    row.updated_by_user_id = actor_user_id
    db.add(row)
    db.flush()
    db.refresh(row)
    return role_policy_response(row)


def reset_role_policy(
    db: Session,
    *,
    role: str,
    expected_revision: int,
    actor_user_id: uuid.UUID,
) -> WorkspaceRolePolicyResponse:
    normalized_role = _workspace_role(role)
    row = db.scalar(
        select(WorkspaceRolePolicy)
        .where(WorkspaceRolePolicy.role == normalized_role)
        .with_for_update()
    )
    if row is None:
        raise WorkspacePolicyUnavailable(
            "The workspace policy for this built-in role is missing. Restore the database or rerun migrations.",
            context={"role": normalized_role},
        )
    if row.revision != expected_revision:
        raise WorkspacePolicyRevisionConflict(
            "The workspace role policy changed after it was loaded. Reload it and retry.",
            current_revision=row.revision,
            context={
                "role": normalized_role,
                "expected_revision": expected_revision,
                "current_revision": row.revision,
            },
        )

    row.modules_json = default_role_modules(normalized_role)
    row.landing_module_id = _DEFAULT_LANDING_MODULE
    row.dashboard_panel_ids_json = list(_DEFAULT_DASHBOARD_PANELS)
    row.revision += 1
    row.updated_by_user_id = actor_user_id
    db.add(row)
    db.flush()
    db.refresh(row)
    return role_policy_response(row)


def get_user_preferences(db: Session, user: User) -> WorkspaceUserPreferenceResponse:
    role = _workspace_role(user.role)
    row = db.get(WorkspaceUserPreference, user.id)
    return user_preference_response(user.id, role, row)


def update_user_preferences(
    db: Session,
    *,
    user: User,
    payload: WorkspaceUserPreferenceWriteRequest,
    actor_user_id: uuid.UUID,
) -> WorkspaceUserPreferenceResponse:
    role = _workspace_role(user.role)
    locked_user = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise WorkspacePolicyUnavailable(
            "The account no longer exists. Sign in again before changing workspace preferences."
        )
    policy_row = db.scalar(
        select(WorkspaceRolePolicy)
        .where(WorkspaceRolePolicy.role == role)
        .with_for_update()
    )
    if policy_row is None:
        raise WorkspacePolicyUnavailable(
            "The workspace policy for this account role is missing. Restore the database or rerun migrations.",
            context={"role": role},
        )
    policy = role_policy_response(policy_row)
    policy_by_id = {module.module_id: module for module in policy.modules}

    row = db.scalar(
        select(WorkspaceUserPreference)
        .where(WorkspaceUserPreference.user_id == user.id)
        .with_for_update()
    )
    current_revision = row.revision if row is not None else 0
    if current_revision != payload.expected_revision:
        raise WorkspacePreferenceRevisionConflict(
            "Workspace preferences changed after they were loaded. Reload them and retry.",
            current_revision=current_revision,
            context={
                "expected_revision": payload.expected_revision,
                "current_revision": current_revision,
            },
        )

    supplied_ids = {module.module_id for module in payload.modules}
    unknown_ids = sorted(supplied_ids - WORKSPACE_MODULE_BY_ID.keys())
    if unknown_ids:
        raise _unknown_module_error(unknown_ids)
    non_customizable = sorted(
        module_id for module_id in supplied_ids if not policy_by_id[module_id].optional
    )
    if non_customizable:
        raise WorkspaceModuleNotCustomizable(
            "One or more modules are fixed by the organization workspace policy.",
            context={"module_ids": non_customizable, "role": role},
        )
    _validate_dashboard_panels(payload.dashboard_panel_ids or [])

    preferences_by_id = {module.module_id: module for module in payload.modules}
    if payload.landing_module_id is not None:
        landing_policy = policy_by_id.get(payload.landing_module_id)
        if landing_policy is None:
            if row is None or payload.landing_module_id != row.landing_module_id:
                raise _unknown_module_error([payload.landing_module_id])
        elif not _preference_landing_available(
            payload.landing_module_id,
            policy_by_id=policy_by_id,
            preferences_by_id=preferences_by_id,
        ):
            raise WorkspaceLandingModuleUnavailable(
                "The preferred landing module and its parent navigation modules must be visible in the effective workspace policy.",
                context={"module_id": payload.landing_module_id},
            )

    unknown_stored_modules = _unknown_mapping_entries(
        row.modules_json if row is not None else {}
    )
    existing_panels = row.dashboard_panel_ids_json if row is not None else None
    unknown_stored_panels = _unknown_string_entries(
        existing_panels or [], WORKSPACE_DASHBOARD_PANEL_BY_ID
    )
    stored_panels = (
        None
        if payload.dashboard_panel_ids is None
        else [*payload.dashboard_panel_ids, *unknown_stored_panels]
    )
    stored_modules = {
        **unknown_stored_modules,
        **{
            module.module_id: {
                key: value
                for key, value in {
                    "visible": module.visible,
                    "order": module.order,
                }.items()
                if value is not None
            }
            for module in payload.modules
        },
    }

    if row is None:
        row = WorkspaceUserPreference(
            user_id=user.id,
            modules_json=stored_modules,
            landing_module_id=payload.landing_module_id,
            dashboard_panel_ids_json=stored_panels,
            revision=1,
            updated_by_user_id=actor_user_id,
        )
    else:
        row.modules_json = stored_modules
        row.landing_module_id = payload.landing_module_id
        row.dashboard_panel_ids_json = stored_panels
        row.revision += 1
        row.updated_by_user_id = actor_user_id
    db.add(row)
    db.flush()
    db.refresh(row)
    return user_preference_response(user.id, role, row)


def reset_user_preferences(
    db: Session,
    *,
    user: User,
    expected_revision: int,
) -> WorkspaceUserPreferenceResponse:
    role = _workspace_role(user.role)
    locked_user = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise WorkspacePolicyUnavailable(
            "The account no longer exists. Sign in again before resetting workspace preferences."
        )
    row = db.scalar(
        select(WorkspaceUserPreference)
        .where(WorkspaceUserPreference.user_id == user.id)
        .with_for_update()
    )
    current_revision = row.revision if row is not None else 0
    if current_revision != expected_revision:
        raise WorkspacePreferenceRevisionConflict(
            "Workspace preferences changed after they were loaded. Reload them and retry.",
            current_revision=current_revision,
            context={
                "expected_revision": expected_revision,
                "current_revision": current_revision,
            },
        )
    if row is not None:
        db.delete(row)
        db.flush()
    return user_preference_response(user.id, role, None)


def effective_workspace(
    db: Session,
    *,
    user: User,
    authorization: AuthorizationContext,
    feature_flags: Mapping[str, bool] | None = None,
) -> WorkspaceEffectiveResponse:
    role = _workspace_role(user.role)
    policy, preferences = _coherent_workspace_state(db, user=user, role=role)
    features = dict(feature_flags or runtime_workspace_feature_flags())
    policy_by_id = {module.module_id: module for module in policy.modules}
    preference_by_id = {module.module_id: module for module in preferences.modules}
    resolved: dict[str, WorkspaceEffectiveModuleResponse] = {}
    warnings = [*policy.warnings, *preferences.warnings]

    for definition in WORKSPACE_MODULES:
        policy_module = policy_by_id[definition.id]
        preference = preference_by_id.get(definition.id)
        preference_applies = preference is not None and policy_module.optional
        if preference is not None and not policy_module.optional:
            warnings.append(f"ignored_non_optional_preference:{definition.id}")
        missing_permissions = [
            permission
            for permission in definition.required_permissions
            if not authorization.has(permission)
        ]
        permission_allowed = not missing_permissions
        feature_available = definition.feature_flag is None or features.get(
            definition.feature_flag, False
        )
        preference_visible = not preference_applies or preference.visible is not False
        reasons: list[str] = []
        if not permission_allowed:
            reasons.append("permission_missing")
        if not feature_available:
            reasons.append("feature_unavailable")
        if not policy_module.visible:
            reasons.append("policy_hidden")
        if not preference_visible:
            reasons.append("preference_hidden")
        if not authorization.account_eligible:
            reasons.append("account_ineligible")
        parent_visible = True
        if definition.parent_id is not None:
            parent_visible = resolved[definition.parent_id].visible
            if not parent_visible:
                reasons.append("parent_hidden")

        resolved[definition.id] = WorkspaceEffectiveModuleResponse(
            id=definition.id,
            label=definition.label,
            route=definition.route,
            section=definition.section,
            parent_id=definition.parent_id,
            visible=(
                authorization.account_eligible
                and permission_allowed
                and feature_available
                and policy_module.visible
                and preference_visible
                and parent_visible
            ),
            optional=policy_module.optional,
            order=(
                preference.order
                if preference_applies and preference.order is not None
                else policy_module.order
            ),
            mobile_priority=policy_module.mobile_priority,
            mobile_behavior=definition.mobile_behavior,
            permission_allowed=permission_allowed,
            missing_permissions=missing_permissions,
            feature_available=feature_available,
            policy_visible=policy_module.visible,
            preference_visible=preference_visible,
            reasons=reasons,
        )

    modules = sorted(
        resolved.values(),
        key=lambda module: (module.section, module.order, module.id),
    )
    preferred_landing = preferences.landing_module_id or policy.landing_module_id
    landing = resolved.get(preferred_landing)
    if landing is None or not landing.visible:
        if preferred_landing:
            warnings.append(f"landing_module_unavailable:{preferred_landing}")
        landing = next(
            (
                module
                for module in modules
                if module.visible and module.section == "primary"
            ),
            next((module for module in modules if module.visible), None),
        )

    configured_panels = (
        preferences.dashboard_panel_ids
        if preferences.dashboard_panel_ids is not None
        else policy.dashboard_panel_ids
    )
    dashboard_panel_details = [
        _dashboard_panel_resolution(
            panel_id,
            authorization=authorization,
            feature_flags=features,
        )
        for panel_id in configured_panels
        if panel_id in WORKSPACE_DASHBOARD_PANEL_BY_ID
    ]
    dashboard_panels = [panel.id for panel in dashboard_panel_details if panel.visible]
    return WorkspaceEffectiveResponse(
        role=role,
        policy_revision=policy.revision,
        preference_revision=preferences.revision,
        landing_module_id=landing.id if landing is not None else None,
        dashboard_panel_ids=dashboard_panels,
        dashboard_panels=dashboard_panel_details,
        modules=modules,
        warnings=sorted(set(warnings)),
    )


def role_policy_response(row: WorkspaceRolePolicy) -> WorkspaceRolePolicyResponse:
    role = _workspace_role(row.role)
    modules, unknown_modules, warnings = _parse_role_modules(role, row.modules_json)
    known_panels, unknown_panels, panel_warnings = _parse_dashboard_panels(
        row.dashboard_panel_ids_json
    )
    warnings.extend(panel_warnings)
    if row.landing_module_id not in WORKSPACE_MODULE_BY_ID:
        unknown_modules = sorted({*unknown_modules, row.landing_module_id})
        warnings.append(f"unknown_landing_module:{row.landing_module_id}")
    return WorkspaceRolePolicyResponse(
        role=role,
        landing_module_id=row.landing_module_id,
        modules=modules,
        dashboard_panel_ids=known_panels,
        revision=row.revision,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        unknown_module_ids=unknown_modules,
        unknown_dashboard_panel_ids=unknown_panels,
        warnings=warnings,
    )


def user_preference_response(
    user_id: uuid.UUID,
    role: WorkspaceRole,
    row: WorkspaceUserPreference | None,
) -> WorkspaceUserPreferenceResponse:
    if row is None:
        return WorkspaceUserPreferenceResponse(
            user_id=user_id,
            role=role,
            landing_module_id=None,
            modules=[],
            dashboard_panel_ids=None,
            revision=0,
        )
    modules, unknown_modules, warnings = _parse_user_modules(row.modules_json)
    if (
        row.landing_module_id is not None
        and row.landing_module_id not in WORKSPACE_MODULE_BY_ID
    ):
        unknown_modules = sorted({*unknown_modules, row.landing_module_id})
        warnings.append(f"unknown_landing_module:{row.landing_module_id}")
    if row.dashboard_panel_ids_json is None:
        panels = None
        unknown_panels: list[str] = []
    else:
        panels, unknown_panels, panel_warnings = _parse_dashboard_panels(
            row.dashboard_panel_ids_json
        )
        warnings.extend(panel_warnings)
    return WorkspaceUserPreferenceResponse(
        user_id=user_id,
        role=role,
        landing_module_id=row.landing_module_id,
        modules=modules,
        dashboard_panel_ids=panels,
        revision=row.revision,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        unknown_module_ids=unknown_modules,
        unknown_dashboard_panel_ids=unknown_panels,
        warnings=warnings,
    )


def _parse_role_modules(
    role: WorkspaceRole, raw: object
) -> tuple[list[WorkspaceModulePolicy], list[str], list[str]]:
    if not isinstance(raw, dict):
        raise WorkspacePolicyUnavailable(
            "Workspace role module policy is malformed. Correct or restore the policy before continuing.",
            context={"role": role},
        )
    defaults = default_role_modules(role)
    unknown = sorted(
        key for key in raw if isinstance(key, str) and key not in WORKSPACE_MODULE_BY_ID
    )
    warnings = [f"unknown_policy_module:{module_id}" for module_id in unknown]
    modules: list[WorkspaceModulePolicy] = []
    for definition in WORKSPACE_MODULES:
        value = raw.get(definition.id, defaults[definition.id])
        parsed = _parse_role_module(definition.id, value)
        if parsed is None:
            warnings.append(f"invalid_policy_module:{definition.id}")
            parsed = WorkspaceModulePolicy(
                module_id=definition.id,
                visible=False,
                optional=False,
                order=definition.default_order,
                mobile_priority=definition.default_mobile_priority,
            )
        modules.append(parsed)
    return modules, unknown, warnings


def _parse_role_module(module_id: str, value: object) -> WorkspaceModulePolicy | None:
    if not isinstance(value, dict):
        return None
    visible = value.get("visible")
    optional = value.get("optional")
    order = value.get("order")
    mobile_priority = value.get("mobile_priority")
    if (
        not isinstance(visible, bool)
        or not isinstance(optional, bool)
        or isinstance(order, bool)
        or not isinstance(order, int)
        or isinstance(mobile_priority, bool)
        or not isinstance(mobile_priority, int)
        or not 0 <= order <= 10_000
        or not 0 <= mobile_priority <= 10_000
    ):
        return None
    return WorkspaceModulePolicy(
        module_id=module_id,
        visible=visible,
        optional=optional,
        order=order,
        mobile_priority=mobile_priority,
    )


def _parse_user_modules(
    raw: object,
) -> tuple[list[WorkspaceModulePreference], list[str], list[str]]:
    if not isinstance(raw, dict):
        raise WorkspacePolicyUnavailable(
            "Workspace user preferences are malformed. Reset the preferences before continuing."
        )
    unknown = sorted(
        key for key in raw if isinstance(key, str) and key not in WORKSPACE_MODULE_BY_ID
    )
    warnings = [f"unknown_preference_module:{module_id}" for module_id in unknown]
    modules: list[WorkspaceModulePreference] = []
    for module_id in sorted(WORKSPACE_MODULE_BY_ID):
        if module_id not in raw:
            continue
        value = raw[module_id]
        if not isinstance(value, dict):
            warnings.append(f"invalid_preference_module:{module_id}")
            continue
        visible = value.get("visible")
        order = value.get("order")
        if visible is not None and not isinstance(visible, bool):
            warnings.append(f"invalid_preference_module:{module_id}")
            continue
        if order is not None and (
            isinstance(order, bool)
            or not isinstance(order, int)
            or not 0 <= order <= 10_000
        ):
            warnings.append(f"invalid_preference_module:{module_id}")
            continue
        if visible is None and order is None:
            warnings.append(f"invalid_preference_module:{module_id}")
            continue
        modules.append(
            WorkspaceModulePreference(
                module_id=module_id,
                visible=visible,
                order=order,
            )
        )
    return modules, unknown, warnings


def _parse_dashboard_panels(
    raw: object,
) -> tuple[list[str], list[str], list[str]]:
    if not isinstance(raw, list):
        raise WorkspacePolicyUnavailable(
            "Workspace dashboard defaults are malformed. Correct or restore the policy before continuing."
        )
    known: list[str] = []
    unknown: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str):
            warnings.append("invalid_dashboard_panel_id")
            continue
        if value in seen:
            warnings.append(f"duplicate_dashboard_panel:{value}")
            continue
        seen.add(value)
        if value in WORKSPACE_DASHBOARD_PANEL_BY_ID:
            known.append(value)
        else:
            unknown.append(value)
            warnings.append(f"unknown_dashboard_panel:{value}")
    return known, sorted(unknown), warnings


def _workspace_role(role: str) -> WorkspaceRole:
    normalized = role.strip().lower()
    if normalized not in WORKSPACE_ROLES:
        raise WorkspaceRoleInvalid(
            "Workspace role defaults exist only for admin, analyst, and viewer.",
            context={"role": normalized},
        )
    return normalized  # type: ignore[return-value]


def _unknown_module_error(module_ids: list[str]) -> WorkspaceUnknownModule:
    return WorkspaceUnknownModule(
        "One or more module IDs are not present in this ThreatLens release's trusted registry.",
        context={"module_ids": sorted(module_ids)},
    )


def _validate_dashboard_panels(panel_ids: list[str]) -> None:
    unknown = sorted(set(panel_ids) - WORKSPACE_DASHBOARD_PANEL_BY_ID.keys())
    if unknown:
        raise WorkspaceUnknownDashboardPanel(
            "One or more dashboard panel IDs are not present in this ThreatLens release's trusted registry.",
            context={"panel_ids": unknown},
        )


def _policy_landing_available(
    module_id: str,
    modules_by_id: Mapping[str, WorkspaceModulePolicy],
) -> bool:
    current_id: str | None = module_id
    seen: set[str] = set()
    while current_id is not None:
        if current_id in seen:
            return False
        seen.add(current_id)
        module = modules_by_id.get(current_id)
        definition = WORKSPACE_MODULE_BY_ID.get(current_id)
        if module is None or definition is None or not module.visible:
            return False
        current_id = definition.parent_id
    return True


def _preference_landing_available(
    module_id: str,
    *,
    policy_by_id: Mapping[str, WorkspaceModulePolicy],
    preferences_by_id: Mapping[str, WorkspaceModulePreference],
) -> bool:
    current_id: str | None = module_id
    seen: set[str] = set()
    while current_id is not None:
        if current_id in seen:
            return False
        seen.add(current_id)
        policy = policy_by_id.get(current_id)
        definition = WORKSPACE_MODULE_BY_ID.get(current_id)
        if policy is None or definition is None or not policy.visible:
            return False
        preference = preferences_by_id.get(current_id)
        if policy.optional and preference is not None and preference.visible is False:
            return False
        current_id = definition.parent_id
    return True


def _unknown_mapping_entries(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise WorkspacePolicyUnavailable(
            "Stored workspace module policy is malformed. Correct or restore it before updating."
        )
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and key not in WORKSPACE_MODULE_BY_ID
    }


def _unknown_string_entries(
    values: object, registry: Mapping[str, object]
) -> list[str]:
    if not isinstance(values, list):
        raise WorkspacePolicyUnavailable(
            "Stored workspace component policy is malformed. Correct or restore it before updating."
        )
    return list(
        dict.fromkeys(
            value
            for value in values
            if isinstance(value, str) and value not in registry
        )
    )


def _coherent_workspace_state(
    db: Session,
    *,
    user: User,
    role: WorkspaceRole,
) -> tuple[WorkspaceRolePolicyResponse, WorkspaceUserPreferenceResponse]:
    for _attempt in range(_WORKSPACE_SNAPSHOT_ATTEMPTS):
        policy_row = db.scalar(
            select(WorkspaceRolePolicy)
            .where(WorkspaceRolePolicy.role == role)
            .execution_options(populate_existing=True)
        )
        if policy_row is None:
            raise WorkspacePolicyUnavailable(
                "The workspace policy for this account role is missing. Restore the database or rerun migrations.",
                context={"role": role},
            )
        policy = role_policy_response(policy_row)

        preference_row = db.scalar(
            select(WorkspaceUserPreference)
            .where(WorkspaceUserPreference.user_id == user.id)
            .execution_options(populate_existing=True)
        )
        preferences = user_preference_response(user.id, role, preference_row)
        if _workspace_revision_pair(db, role=role, user_id=user.id) == (
            policy.revision,
            preferences.revision,
        ):
            return policy, preferences
        db.expire_all()
    raise WorkspaceSnapshotUnavailable(
        "Workspace policy changed repeatedly while the effective workspace was evaluated. Retry the request.",
        context={"attempts": _WORKSPACE_SNAPSHOT_ATTEMPTS, "role": role},
    )


def _workspace_revision_pair(
    db: Session,
    *,
    role: WorkspaceRole,
    user_id: uuid.UUID,
) -> tuple[int | None, int]:
    role_revision = db.scalar(
        select(WorkspaceRolePolicy.revision).where(WorkspaceRolePolicy.role == role)
    )
    preference_revision = db.scalar(
        select(WorkspaceUserPreference.revision).where(
            WorkspaceUserPreference.user_id == user_id
        )
    )
    return role_revision, preference_revision or 0


def _dashboard_panel_resolution(
    panel_id: str,
    *,
    authorization: AuthorizationContext,
    feature_flags: Mapping[str, bool],
) -> WorkspaceEffectiveDashboardPanelResponse:
    panel = WORKSPACE_DASHBOARD_PANEL_BY_ID.get(panel_id)
    if panel is None:
        return WorkspaceEffectiveDashboardPanelResponse(
            id=panel_id,
            visible=False,
            permission_allowed=False,
            feature_available=False,
            reasons=["unknown_panel"],
        )
    missing_permissions = [
        permission
        for permission in panel.required_permissions
        if not authorization.has(permission)
    ]
    permission_allowed = not missing_permissions
    feature_available = panel.feature_flag is None or feature_flags.get(
        panel.feature_flag, False
    )
    reasons: list[str] = []
    if not permission_allowed:
        reasons.append("permission_missing")
    if not feature_available:
        reasons.append("feature_unavailable")
    if not authorization.account_eligible:
        reasons.append("account_ineligible")
    return WorkspaceEffectiveDashboardPanelResponse(
        id=panel.id,
        visible=(
            authorization.account_eligible and permission_allowed and feature_available
        ),
        permission_allowed=permission_allowed,
        feature_available=feature_available,
        missing_permissions=missing_permissions,
        reasons=reasons,
    )


__all__ = [
    "WORKSPACE_DASHBOARD_PANELS",
    "WORKSPACE_MODULES",
    "WORKSPACE_ROLES",
    "WorkspaceLandingModuleUnavailable",
    "WorkspaceModuleNotCustomizable",
    "WorkspacePolicyError",
    "WorkspacePolicyModuleSetIncomplete",
    "WorkspacePolicyRevisionConflict",
    "WorkspaceSnapshotUnavailable",
    "WorkspacePolicyUnavailable",
    "WorkspacePreferenceRevisionConflict",
    "WorkspaceRoleInvalid",
    "WorkspaceUnknownDashboardPanel",
    "WorkspaceUnknownModule",
    "default_role_modules",
    "effective_workspace",
    "get_role_policy",
    "get_user_preferences",
    "list_role_policies",
    "runtime_workspace_feature_flags",
    "reset_role_policy",
    "reset_user_preferences",
    "update_role_policy",
    "update_user_preferences",
    "workspace_registry_response",
]
