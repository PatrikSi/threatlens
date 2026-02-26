"""feed metadata and schedule fields

Revision ID: 0003_feed_metadata_and_schedule
Revises: 0002_enterprise_security
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_feed_metadata_and_schedule"
down_revision = "0002_enterprise_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("feeds", sa.Column("site_url", sa.Text(), nullable=True))
    op.add_column("feeds", sa.Column("language", sa.String(length=64), nullable=True))
    op.add_column("feeds", sa.Column("fetch_mode", sa.String(length=16), nullable=False, server_default="interval"))
    op.add_column("feeds", sa.Column("schedule_cron", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_feeds_fetch_mode",
        "feeds",
        "fetch_mode IN ('interval', 'schedule')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_feeds_fetch_mode", "feeds", type_="check")
    op.drop_column("feeds", "schedule_cron")
    op.drop_column("feeds", "fetch_mode")
    op.drop_column("feeds", "language")
    op.drop_column("feeds", "site_url")
    op.drop_column("feeds", "description")
