"""Retain team destinations and automation receipts across personal offboarding."""

from alembic import op
import sqlalchemy as sa

revision = "0116_team_integrations"
down_revision = "0115_reviewed_publications"
branch_labels = None
depends_on = None


def _owner_fk(table: str, column: str, *, retain: bool) -> None:
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(table)
    name = next(
        fk["name"] for fk in foreign_keys if fk["constrained_columns"] == [column]
    )
    op.drop_constraint(name, table, type_="foreignkey")
    op.alter_column(table, column, nullable=retain or table == "integration_instances")
    op.create_foreign_key(
        name,
        table,
        "users",
        [column],
        ["id"],
        ondelete="SET NULL" if retain else "CASCADE",
    )


def upgrade() -> None:
    for table, column in (
        ("notification_webhooks", "user_id"),
        ("integration_instances", "owner_user_id"),
        ("automation_executions", "owner_user_id"),
        ("webhook_credential_profiles", "user_id"),
    ):
        _owner_fk(table, column, retain=True)
    for table in ("notification_webhooks", "automation_executions"):
        op.add_column(
            table,
            sa.Column(
                "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="RESTRICT")
            ),
        )
        op.create_index(f"ix_{table}_team_id", table, ["team_id"])
    op.add_column(
        "notification_webhooks",
        sa.Column(
            "ownership_revision", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "automation_executions", sa.Column("archived_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_automation_executions_archived_at", "automation_executions", ["archived_at"]
    )
    op.create_table(
        "automation_receiver_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    for column in ("webhook_id", "team_id"):
        op.create_index(
            f"ix_automation_receiver_credentials_{column}",
            "automation_receiver_credentials",
            [column],
        )
    op.execute("""
        CREATE FUNCTION retain_team_integrations_on_user_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM automation_executions WHERE owner_user_id = OLD.id AND team_id IS NULL;
            DELETE FROM integration_instances AS i WHERE i.owner_user_id = OLD.id
                AND NOT EXISTS (SELECT 1 FROM notification_webhooks AS w
                    WHERE w.integration_id = i.id AND w.team_id IS NOT NULL);
            DELETE FROM notification_webhooks WHERE user_id = OLD.id AND team_id IS NULL;
            DELETE FROM webhook_credential_profiles AS p WHERE p.user_id = OLD.id
                AND NOT EXISTS (SELECT 1 FROM notification_webhooks AS w
                    WHERE w.credential_profile_id = p.id);
            RETURN OLD;
        END $$;
        CREATE TRIGGER retain_team_integrations_before_user_delete
            BEFORE DELETE ON users FOR EACH ROW
            EXECUTE FUNCTION retain_team_integrations_on_user_delete();
    """)


def downgrade() -> None:
    db = op.get_bind()
    for table, column in (
        ("notification_webhooks", "user_id"),
        ("automation_executions", "owner_user_id"),
        ("webhook_credential_profiles", "user_id"),
    ):
        if db.scalar(
            sa.text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {column} IS NULL)")
        ):
            raise RuntimeError(
                "Cannot downgrade while orphaned retained integration history exists"
            )
    for table in ("notification_webhooks", "automation_executions"):
        if db.scalar(
            sa.text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE team_id IS NOT NULL)")
        ):
            raise RuntimeError(
                "Cannot downgrade while team integrations or history exist"
            )
    if db.scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM automation_receiver_credentials)")
    ):
        raise RuntimeError("Cannot downgrade while receiver credentials exist")
    op.execute("DROP TRIGGER retain_team_integrations_before_user_delete ON users")
    op.execute("DROP FUNCTION retain_team_integrations_on_user_delete()")
    op.drop_table("automation_receiver_credentials")
    op.drop_index(
        "ix_automation_executions_archived_at", table_name="automation_executions"
    )
    op.drop_column("automation_executions", "archived_at")
    op.drop_column("notification_webhooks", "ownership_revision")
    for table in ("notification_webhooks", "automation_executions"):
        op.drop_index(f"ix_{table}_team_id", table_name=table)
        op.drop_column(table, "team_id")
    for table, column in (
        ("notification_webhooks", "user_id"),
        ("integration_instances", "owner_user_id"),
        ("automation_executions", "owner_user_id"),
        ("webhook_credential_profiles", "user_id"),
    ):
        _owner_fk(table, column, retain=False)
