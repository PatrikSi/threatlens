import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


_JSON = JSON().with_variant(JSONB(), "postgresql")


class WorkspaceRolePolicy(Base):
    __tablename__ = "workspace_role_policies"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'analyst', 'viewer')",
            name="ck_workspace_role_policies_role",
        ),
        CheckConstraint(
            "jsonb_typeof(modules_json) = 'object'",
            name="ck_workspace_role_policies_modules_object",
        ),
        CheckConstraint(
            "jsonb_typeof(dashboard_panel_ids_json) = 'array'",
            name="ck_workspace_role_policies_dashboard_panels_array",
        ),
        CheckConstraint("revision >= 1", name="ck_workspace_role_policies_revision"),
    )

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    modules_json: Mapped[dict[str, dict[str, object]]] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    landing_module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dashboard_panel_ids_json: Mapped[list[str]] = mapped_column(
        _JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class WorkspaceUserPreference(Base):
    __tablename__ = "workspace_user_preferences"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(modules_json) = 'object'",
            name="ck_workspace_user_preferences_modules_object",
        ),
        CheckConstraint(
            "dashboard_panel_ids_json IS NULL OR "
            "jsonb_typeof(dashboard_panel_ids_json) = 'array'",
            name="ck_workspace_user_preferences_dashboard_panels_array",
        ),
        CheckConstraint("revision >= 1", name="ck_workspace_user_preferences_revision"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    modules_json: Mapped[dict[str, dict[str, object]]] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    landing_module_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dashboard_panel_ids_json: Mapped[list[str] | None] = mapped_column(
        _JSON, nullable=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["WorkspaceRolePolicy", "WorkspaceUserPreference"]
