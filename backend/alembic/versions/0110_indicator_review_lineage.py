"""Retain all handling labels used by indicator review history."""

from alembic import op
import sqlalchemy as sa

revision = "0110_indicator_review_lineage"
down_revision = "0109_article_preview_privacy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indicator_assessment_labels",
        sa.Column(
            "assessment_id",
            sa.Uuid(),
            sa.ForeignKey("indicator_assessments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "handling_label_id",
            sa.Uuid(),
            sa.ForeignKey("handling_labels.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_indicator_assessment_labels_handling_label_id",
        "indicator_assessment_labels",
        ["handling_label_id"],
    )
    op.execute(
        "INSERT INTO indicator_assessment_labels (assessment_id, handling_label_id) "
        "SELECT assessment.id, label.id FROM indicator_assessments assessment "
        "JOIN handling_labels label ON assessment.version > 1 "
        "OR label.id = assessment.handling_label_id"
    )


def downgrade() -> None:
    # Earlier releases have only one captured label. Dropping a wider boundary
    # would silently disclose retained review notes to less privileged readers.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM indicator_assessment_labels labels "
            "JOIN indicator_assessments assessment ON assessment.id = labels.assessment_id "
            "WHERE labels.handling_label_id <> assessment.handling_label_id)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade while indicator assessments retain multiple handling labels. "
            "Remove the affected retained assessments before downgrading."
        )
    op.drop_index(
        "ix_indicator_assessment_labels_handling_label_id",
        "indicator_assessment_labels",
    )
    op.drop_table("indicator_assessment_labels")
