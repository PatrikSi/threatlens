"""Pin report review and publication to exact content and evidence revisions."""

from alembic import op
import sqlalchemy as sa

revision = "0104_report_editorial_lifecycle"
down_revision = "0103_shared_triage_queues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("reports", "report_schedules"):
        # Retain explicitly configured legacy automation during the upgrade.
        op.add_column(
            table,
            sa.Column(
                "review_required", sa.Boolean(), server_default="false", nullable=False
            ),
        )
        op.alter_column(table, "review_required", server_default="true")
    op.add_column(
        "reports",
        sa.Column(
            "publication_status", sa.String(16), server_default="draft", nullable=False
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "editorial_version", sa.Integer(), server_default="1", nullable=False
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "editorial_contract_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.alter_column("reports", "editorial_contract_version", server_default="1")
    op.add_column(
        "reports",
        sa.Column(
            "approval_self_review", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    for column in (
        "review_revision_hash",
        "approved_revision_hash",
        "published_revision_hash",
    ):
        op.add_column("reports", sa.Column(column, sa.String(64), nullable=True))
    for stage in ("last_edited", "review_submitted", "approved", "published"):
        op.add_column(
            "reports",
            sa.Column(f"{stage}_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            "reports", sa.Column(f"{stage}_by_user_id", sa.Uuid(), nullable=True)
        )
        op.create_foreign_key(
            f"fk_reports_{stage}_by_user_id_users",
            "reports",
            "users",
            [f"{stage}_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column("reports", sa.Column("editorial_note", sa.Text(), nullable=True))
    op.execute(
        "UPDATE reports SET publication_status='published', published_at=generated_at WHERE status='ready'"
    )
    op.create_check_constraint(
        "ck_reports_publication_status",
        "reports",
        "publication_status IN ('draft', 'review', 'approved', 'published')",
    )
    op.create_check_constraint(
        "ck_reports_editorial_version", "reports", "editorial_version >= 1"
    )
    op.create_index("ix_reports_publication_status", "reports", ["publication_status"])


def downgrade() -> None:
    # Old binaries cannot retain the meaning of reviewed publication pins. Never
    # discard their history merely because a report has reached published state.
    bind = op.get_bind()
    history = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM reports WHERE editorial_version > 1 OR published_revision_hash IS NOT NULL)"
        )
    ).scalar()
    if history:
        raise RuntimeError(
            "Back up and remove reports with editorial history before downgrading; "
            "published approval and evidence revisions must not be discarded silently."
        )
    pending = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM reports WHERE review_required AND publication_status != 'published') OR EXISTS (SELECT 1 FROM report_schedules WHERE review_required)"
        )
    ).scalar()
    if pending:
        raise RuntimeError(
            "Back up and remove reports awaiting editorial review, and disable "
            "schedule review before downgrading. Publishing does not remove "
            "the protection of retained editorial history."
        )
    op.drop_index("ix_reports_publication_status", table_name="reports")
    op.drop_constraint("ck_reports_editorial_version", "reports", type_="check")
    op.drop_constraint("ck_reports_publication_status", "reports", type_="check")
    for stage in ("last_edited", "review_submitted", "approved", "published"):
        op.drop_constraint(
            f"fk_reports_{stage}_by_user_id_users", "reports", type_="foreignkey"
        )
        op.drop_column("reports", f"{stage}_by_user_id")
        op.drop_column("reports", f"{stage}_at")
    for column in (
        "editorial_note",
        "review_revision_hash",
        "approved_revision_hash",
        "published_revision_hash",
        "approval_self_review",
        "editorial_contract_version",
        "editorial_version",
        "publication_status",
        "review_required",
    ):
        op.drop_column("reports", column)
    op.drop_column("report_schedules", "review_required")
