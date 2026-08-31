"""add handling-label data policy foundation

Revision ID: 0069_data_policy_foundation
Revises: 0068_access_reviews
Create Date: 2026-08-30
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = "0069_data_policy_foundation"
down_revision = "0068_access_reviews"
branch_labels = None
depends_on = None


UNRESTRICTED_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000201")


def upgrade() -> None:
    op.create_table(
        "data_policy_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "mode", sa.String(length=16), nullable=False, server_default="disabled"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("coverage_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enforced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enforced_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_data_policy_state_singleton"),
        sa.CheckConstraint(
            "mode IN ('disabled', 'audit', 'enforced')",
            name="ck_data_policy_state_mode",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_data_policy_state_revision"),
        sa.CheckConstraint(
            "coverage_version >= 0", name="ck_data_policy_state_coverage_version"
        ),
        sa.CheckConstraint(
            "(mode = 'enforced' AND enforced_at IS NOT NULL "
            "AND enforced_by_user_id IS NOT NULL) OR "
            "(mode <> 'enforced' AND enforced_at IS NULL "
            "AND enforced_by_user_id IS NULL)",
            name="ck_data_policy_state_enforcement_bundle",
        ),
        sa.ForeignKeyConstraint(
            ["enforced_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "handling_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "color", sa.String(length=7), nullable=False, server_default="#64748B"
        ),
        sa.Column(
            "is_unrestricted", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
            "key = lower(key) AND key = btrim(key) "
            "AND key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
            name="ck_handling_labels_key",
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND length(name) BETWEEN 1 AND 120 "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_handling_labels_name",
        ),
        sa.CheckConstraint(
            "description = btrim(description) AND length(description) <= 2000",
            name="ck_handling_labels_description",
        ),
        sa.CheckConstraint(
            "color ~ '^#[0-9A-Fa-f]{6}$'", name="ck_handling_labels_color"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_handling_labels_revision"),
        sa.CheckConstraint(
            "(id = '00000000-0000-4000-8000-000000000201'::uuid "
            "AND key = 'unrestricted' AND is_unrestricted AND is_system "
            "AND is_active) OR "
            "(id <> '00000000-0000-4000-8000-000000000201'::uuid "
            "AND NOT is_unrestricted)",
            name="ck_handling_labels_unrestricted_identity",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_handling_labels_key"),
    )
    op.create_index(
        "ix_handling_labels_active_name",
        "handling_labels",
        ["is_active", "name"],
    )
    op.create_index(
        "uq_handling_labels_unrestricted",
        "handling_labels",
        ["is_unrestricted"],
        unique=True,
        postgresql_where=sa.text("is_unrestricted"),
    )
    op.create_table(
        "data_policy_role_grants",
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["handling_labels.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["iam_roles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("label_id", "role_id"),
    )
    op.create_index(
        "ix_data_policy_role_grants_role",
        "data_policy_role_grants",
        ["role_id", "label_id"],
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO data_policy_state "
            "(id, mode, revision, coverage_version) "
            "VALUES (1, 'disabled', 1, 0)"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO handling_labels "
            "(id, key, name, description, color, is_unrestricted, "
            "is_system, is_active, revision) "
            "VALUES (:id, 'unrestricted', 'Unrestricted', "
            "'Available to every authenticated principal with access to the resource.', "
            "'#64748B', true, true, true, 1)"
        ),
        {"id": UNRESTRICTED_LABEL_ID},
    )

    unrestricted_literal = str(UNRESTRICTED_LABEL_ID)
    op.add_column(
        "feeds",
        sa.Column(
            "handling_label_id",
            sa.Uuid(),
            nullable=False,
            server_default=unrestricted_literal,
        ),
    )
    op.create_foreign_key(
        "fk_feeds_handling_label_id",
        "feeds",
        "handling_labels",
        ["handling_label_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_feeds_handling_label_id", "feeds", ["handling_label_id"])

    op.execute(
        """
        CREATE FUNCTION protect_unrestricted_handling_label()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.id =
               '00000000-0000-4000-8000-000000000201'::uuid THEN
                RAISE EXCEPTION 'the unrestricted handling label cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.id =
               '00000000-0000-4000-8000-000000000201'::uuid AND (
                NEW.id <> OLD.id OR NEW.key <> 'unrestricted' OR
                NOT NEW.is_unrestricted OR NOT NEW.is_system OR
                NOT NEW.is_active
            ) THEN
                RAISE EXCEPTION 'the unrestricted handling label identity is immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_unrestricted_handling_label
        BEFORE UPDATE OR DELETE ON handling_labels
        FOR EACH ROW EXECUTE FUNCTION protect_unrestricted_handling_label()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_data_policy_state()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'the data policy state cannot be deleted';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_data_policy_state
        BEFORE DELETE ON data_policy_state
        FOR EACH ROW EXECUTE FUNCTION protect_data_policy_state()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    state = (
        bind.execute(
            sa.text(
                "SELECT mode, revision, coverage_version FROM data_policy_state WHERE id = 1"
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        raise RuntimeError(
            "Cannot downgrade data policies because the singleton policy state is missing."
        )
    if (
        state["mode"] != "disabled"
        or state["revision"] != 1
        or state["coverage_version"] != 0
    ):
        raise RuntimeError(
            "Cannot downgrade data policies after policy state has been changed. "
            "Return to the original disabled revision before retrying."
        )
    custom_label_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM handling_labels WHERE id <> :unrestricted_label_id"
        ),
        {"unrestricted_label_id": UNRESTRICTED_LABEL_ID},
    )
    if custom_label_count:
        raise RuntimeError(
            "Cannot downgrade data policies while custom handling labels exist."
        )
    non_default_feed_count = bind.scalar(
        sa.text("SELECT count(*) FROM feeds WHERE handling_label_id <> :label_id"),
        {"label_id": UNRESTRICTED_LABEL_ID},
    )
    if non_default_feed_count:
        raise RuntimeError(
            "Cannot downgrade data policies while feeds use restricted handling labels."
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_data_policy_state ON data_policy_state"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_data_policy_state()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_unrestricted_handling_label "
        "ON handling_labels"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_unrestricted_handling_label()")
    op.drop_index("ix_feeds_handling_label_id", table_name="feeds")
    op.drop_constraint("fk_feeds_handling_label_id", "feeds", type_="foreignkey")
    op.drop_column("feeds", "handling_label_id")
    op.drop_index(
        "ix_data_policy_role_grants_role", table_name="data_policy_role_grants"
    )
    op.drop_table("data_policy_role_grants")
    op.drop_index("uq_handling_labels_unrestricted", table_name="handling_labels")
    op.drop_index("ix_handling_labels_active_name", table_name="handling_labels")
    op.drop_table("handling_labels")
    op.drop_table("data_policy_state")
