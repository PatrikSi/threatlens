"""add revisioned workspace policy and user preferences

Revision ID: 0063_workspace_policy
Revises: 0062_access_roles_groups
Create Date: 2026-08-30
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0063_workspace_policy"
down_revision = "0062_access_roles_groups"
branch_labels = None
depends_on = None


_SYSTEM_ROLE_IDS = {
    "analyst": uuid.UUID("00000000-0000-4000-8000-000000000002"),
    "viewer": uuid.UUID("00000000-0000-4000-8000-000000000003"),
}
_ROLES = ("admin", "analyst", "viewer")
_MODULE_DEFAULTS = (
    ("primary.dashboard", "all", False, 0, 0),
    ("primary.alerts", "all", True, 10, 10),
    ("primary.investigations", "all", True, 20, 20),
    ("primary.feeds", "all", True, 30, 30),
    ("primary.stats", "all", True, 40, 40),
    ("primary.export", "all", True, 50, 50),
    ("primary.reporting", "all", True, 60, 60),
    ("primary.settings", "all", False, 70, 70),
    ("settings.account", "all", False, 0, 0),
    ("settings.tokens", "all", True, 10, 10),
    ("settings.ai", "admin", True, 20, 20),
    ("settings.tagging", "admin", True, 30, 30),
    ("settings.identity", "admin", True, 40, 40),
    ("settings.users", "admin", True, 50, 50),
    ("settings.audit", "admin", True, 60, 60),
    ("settings.operations", "admin", True, 70, 70),
    ("settings.integrations", "all", True, 80, 80),
    ("settings.integrations.webhooks", "all", True, 90, 90),
    ("settings.integrations.smtp", "admin", True, 100, 100),
)
_DEFAULT_LANDING_MODULE = "primary.dashboard"
_DEFAULT_DASHBOARD_PANELS = ["rss"]
_LEGACY_ROLE_WORKSPACE_PERMISSIONS = (
    "read:workspace",
    "write:workspace_preferences",
)


def upgrade() -> None:
    op.create_table(
        "workspace_role_policies",
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "modules_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("landing_module_id", sa.String(length=64), nullable=False),
        sa.Column(
            "dashboard_panel_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'analyst', 'viewer')",
            name="ck_workspace_role_policies_role",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(modules_json) = 'object'",
            name="ck_workspace_role_policies_modules_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dashboard_panel_ids_json) = 'array'",
            name="ck_workspace_role_policies_dashboard_panels_array",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_workspace_role_policies_revision"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("role"),
    )
    op.create_table(
        "workspace_user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "modules_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("landing_module_id", sa.String(length=64), nullable=True),
        sa.Column(
            "dashboard_panel_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(modules_json) = 'object'",
            name="ck_workspace_user_preferences_modules_object",
        ),
        sa.CheckConstraint(
            "dashboard_panel_ids_json IS NULL OR "
            "jsonb_typeof(dashboard_panel_ids_json) = 'array'",
            name="ck_workspace_user_preferences_dashboard_panels_array",
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_workspace_user_preferences_revision"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    role_policy_table = sa.table(
        "workspace_role_policies",
        sa.column("role", sa.String()),
        sa.column("modules_json", postgresql.JSONB()),
        sa.column("landing_module_id", sa.String()),
        sa.column("dashboard_panel_ids_json", postgresql.JSONB()),
        sa.column("revision", sa.Integer()),
    )
    op.bulk_insert(
        role_policy_table,
        [
            {
                "role": role,
                "modules_json": _default_modules(role),
                "landing_module_id": _DEFAULT_LANDING_MODULE,
                "dashboard_panel_ids_json": _DEFAULT_DASHBOARD_PANELS,
                "revision": 1,
            }
            for role in _ROLES
        ],
    )
    _grant_workspace_permissions_to_legacy_roles(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    preference_count = int(
        bind.scalar(sa.text("SELECT count(*) FROM workspace_user_preferences")) or 0
    )
    policy_rows = bind.execute(
        sa.text(
            "SELECT role, modules_json, landing_module_id, "
            "dashboard_panel_ids_json, revision, updated_by_user_id "
            "FROM workspace_role_policies"
        )
    ).mappings()
    policies = {row["role"]: row for row in policy_rows}
    customized_roles = [
        role
        for role in _ROLES
        if role not in policies or not _is_default_policy(role, policies[role])
    ]
    extra_roles = sorted(set(policies) - set(_ROLES))
    if preference_count or customized_roles or extra_roles:
        details = []
        if preference_count:
            details.append(f"{preference_count} user preference row(s)")
        if customized_roles:
            details.append(f"customized role policy: {', '.join(customized_roles)}")
        if extra_roles:
            details.append(f"unexpected role policy: {', '.join(extra_roles)}")
        raise RuntimeError(
            "Cannot downgrade workspace policy while persisted workspace state would be lost "
            f"({'; '.join(details)}). Export or reset workspace policy and preferences, "
            "verify the reset, and retry the downgrade. Audit records are preserved separately."
        )

    bind.execute(
        sa.text(
            "DELETE FROM iam_role_permissions "
            "WHERE permission IN ('read:workspace', 'write:workspace_preferences') "
            "AND role_id IN (:analyst, :viewer)"
        ),
        _SYSTEM_ROLE_IDS,
    )
    op.drop_table("workspace_user_preferences")
    op.drop_table("workspace_role_policies")


def _default_modules(role: str) -> dict[str, dict[str, object]]:
    return {
        module_id: {
            "visible": visibility == "all" or role == "admin",
            "optional": optional,
            "order": order,
            "mobile_priority": mobile_priority,
        }
        for module_id, visibility, optional, order, mobile_priority in _MODULE_DEFAULTS
    }


def _grant_workspace_permissions_to_legacy_roles(bind) -> None:
    for role_id in _SYSTEM_ROLE_IDS.values():
        for permission in _LEGACY_ROLE_WORKSPACE_PERMISSIONS:
            bind.execute(
                sa.text(
                    "INSERT INTO iam_role_permissions (role_id, permission) "
                    "VALUES (:role_id, :permission) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "permission": permission},
            )


def _is_default_policy(role: str, row) -> bool:
    return (
        row["landing_module_id"] == _DEFAULT_LANDING_MODULE
        and row["dashboard_panel_ids_json"] == _DEFAULT_DASHBOARD_PANELS
        and row["modules_json"] == _default_modules(role)
    )
