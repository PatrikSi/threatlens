"""Remember personal consent for external article-preview resources."""

from alembic import op
import sqlalchemy as sa

revision = "0109_article_preview_privacy"
down_revision = "0108_webhook_automation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workspace_user_preferences",
        sa.Column(
            "article_preview_external_resources",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("workspace_user_preferences", "article_preview_external_resources")
