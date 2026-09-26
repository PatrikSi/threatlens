"""Track receiver execution independently from delivery and retain policy receipts."""

from alembic import op
import sqlalchemy as sa

revision = "0112_automation_executions"
down_revision = "0111_extraction_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "webhook_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("integration_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("external_job_id", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings", sa.Text()),
        sa.Column(
            "investigation_note_id",
            sa.Uuid(),
            sa.ForeignKey("investigation_notes.id", ondelete="SET NULL"),
        ),
        sa.Column("policy_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "policy_acknowledged_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "policy_state", sa.String(16), nullable=False, server_default="current"
        ),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
        sa.UniqueConstraint(
            "webhook_id", "action_id", name="uq_automation_execution_action"
        ),
    )
    for column in ("webhook_id", "owner_user_id", "next_check_at"):
        op.create_index(
            f"ix_automation_executions_{column}", "automation_executions", [column]
        )
    op.create_table(
        "automation_callbacks",
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("automation_executions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("callback_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "execution_id", "sequence", name="uq_automation_callback_sequence"
        ),
    )
    op.create_table(
        "automation_policy_updates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("automation_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("replacement_action_id", sa.String(128)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "execution_id", "revision", name="uq_automation_policy_revision"
        ),
    )
    op.create_index(
        "ix_automation_policy_updates_execution_id",
        "automation_policy_updates",
        ["execution_id"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM automation_executions)")
    ):
        raise RuntimeError(
            "Cannot discard receiver receipts during downgrade. Export and explicitly clear automation execution history first."
        )
    for table in (
        "automation_policy_updates",
        "automation_callbacks",
        "automation_executions",
    ):
        op.drop_table(table)
