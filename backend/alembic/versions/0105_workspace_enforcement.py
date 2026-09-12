"""Add administrator-enforced workspace defaults and immutable dashboard templates."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0105_workspace_enforcement"
down_revision = "0103_shared_triage_queues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_role_policies",
        sa.Column(
            "landing_mode", sa.String(16), nullable=False, server_default="default"
        ),
    )
    op.add_column(
        "workspace_role_policies",
        sa.Column(
            "dashboard_mode", sa.String(16), nullable=False, server_default="default"
        ),
    )
    op.add_column(
        "workspace_role_policies",
        sa.Column("dashboard_view_json", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_workspace_role_policies_modes",
        "workspace_role_policies",
        "landing_mode IN ('default', 'enforced') AND dashboard_mode IN ('default', 'enforced')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workspace_role_policies_modes", "workspace_role_policies")
    op.drop_column("workspace_role_policies", "dashboard_view_json")
    op.drop_column("workspace_role_policies", "dashboard_mode")
    op.drop_column("workspace_role_policies", "landing_mode")
