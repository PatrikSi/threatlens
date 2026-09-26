"""Add granular automation subscriptions and reusable outbound credentials."""

from alembic import op
import sqlalchemy as sa

revision = "0108_webhook_automation"
down_revision = "0107_intel_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_credential_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auth_type", sa.String(16), nullable=False, server_default="none"),
        sa.Column("header_name", sa.String(128)),
        sa.Column("auth_secret_encrypted", sa.Text()),
        sa.Column("signing_secret_encrypted", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
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
    )
    op.create_index(
        "ix_webhook_credential_profiles_user_id",
        "webhook_credential_profiles",
        ["user_id"],
    )
    op.add_column(
        "notification_webhooks",
        sa.Column(
            "payload_mode", sa.String(32), nullable=False, server_default="template"
        ),
    )
    op.add_column("notification_webhooks", sa.Column("conditions_json", sa.JSON()))
    op.add_column(
        "notification_webhooks", sa.Column("credential_profile_id", sa.Uuid())
    )
    op.create_foreign_key(
        "fk_webhook_credential_profile",
        "notification_webhooks",
        "webhook_credential_profiles",
        ["credential_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_notification_webhooks_credential_profile_id",
        "notification_webhooks",
        ["credential_profile_id"],
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE notification_webhooks, webhook_credential_profiles IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM notification_webhooks WHERE payload_mode <> 'template' OR conditions_json IS NOT NULL OR credential_profile_id IS NOT NULL) OR EXISTS (SELECT 1 FROM webhook_credential_profiles)"
        )
    ):
        raise RuntimeError(
            "Retire automation subscriptions and credential profiles before downgrading; older workers cannot enforce their conditions or credentials."
        )
    op.drop_index(
        "ix_notification_webhooks_credential_profile_id",
        table_name="notification_webhooks",
    )
    op.drop_constraint(
        "fk_webhook_credential_profile", "notification_webhooks", type_="foreignkey"
    )
    for column in ("credential_profile_id", "conditions_json", "payload_mode"):
        op.drop_column("notification_webhooks", column)
    op.drop_table("webhook_credential_profiles")
