"""Register publication consumers and retain acknowledgement obligations."""

from alembic import op
import sqlalchemy as sa

revision = "0121_publication_distribution"
down_revision = "0120_hunt_review_workflow"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "publication_consumers",
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "team_id", "idempotency_key", name="uq_publication_consumer_request"
        ),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "principal_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("authorization_encrypted", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("replay_floor", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_publication_consumer_reconcile",
        "publication_consumers",
        ["last_reconciled_at", "id"],
    )
    op.create_index(
        "ix_publication_consumers_team_id", "publication_consumers", ["team_id"]
    )
    op.create_table(
        "publication_subscriptions",
        sa.Column(
            "consumer_id",
            sa.Uuid(),
            sa.ForeignKey("publication_consumers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "publication_id",
            sa.Uuid(),
            sa.ForeignKey("indicator_publications.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("last_revision", sa.Integer(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_publication_subscription_check",
        "publication_subscriptions",
        ["next_check_at", "consumer_id", "publication_id"],
    )
    op.create_table(
        "publication_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "consumer_id",
            sa.Uuid(),
            sa.ForeignKey("publication_consumers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "consumer_id", "sequence", name="uq_publication_change_sequence"
        ),
    )
    op.create_index(
        "ix_publication_change_ack",
        "publication_changes",
        ["acknowledged_at", "created_at"],
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM publication_consumers)")
    ):
        raise RuntimeError(
            "Archive publication consumers and resolve withdrawal obligations before downgrade"
        )
    op.drop_table("publication_changes")
    op.drop_table("publication_subscriptions")
    op.drop_table("publication_consumers")
