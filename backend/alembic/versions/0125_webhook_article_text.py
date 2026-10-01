"""Add explicit opt-in for extracted article text in automation deliveries."""

from alembic import op
import sqlalchemy as sa

revision = "0125_webhook_article_text"
down_revision = "0124_reconciliation_progress"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "notification_webhooks",
        sa.Column(
            "include_article_text",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("notification_webhooks", "include_article_text")
