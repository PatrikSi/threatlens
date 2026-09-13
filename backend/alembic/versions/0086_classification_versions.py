"""Persist classification source revisions and reconcile legacy stale results.

Revision ID: 0086_classification_versions
Revises: 0085_lifecycle_management
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0086_classification_versions"
down_revision = "0085_lifecycle_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("classification_required_version", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column("items", sa.Column("classification_completed_version", sa.BigInteger(), nullable=False, server_default="0"))
    # Match compute_classification_source_hash without loading article bodies
    # into the migration process. Deliberately purged content keeps its retained
    # classification; retention is not a request to reclassify absent evidence.
    op.execute(sa.text("""
        UPDATE items AS target
        SET classification_completed_version = 1
        FROM items AS source
        JOIN item_classifications AS classification ON classification.item_id = source.id
        LEFT JOIN articles AS article ON article.item_id = source.id
        WHERE target.id = source.id
          AND (
            article.content_purged_at IS NOT NULL
            OR classification.source_hash = encode(sha256(convert_to(
                coalesce(source.title, '') || chr(10) ||
                coalesce(source.summary, '') || chr(10) ||
                coalesce(article.text, ''), 'UTF8'
            )), 'hex')
          )
    """))
    op.create_check_constraint(
        "ck_items_classification_versions", "items",
        "classification_required_version >= 1 AND classification_completed_version >= 0 "
        "AND classification_completed_version <= classification_required_version",
    )
    op.create_index(
        "ix_items_pending_classification", "items", ["first_seen_at", "id"],
        postgresql_where=sa.text("classification_completed_version < classification_required_version"),
    )


def downgrade() -> None:
    op.drop_index("ix_items_pending_classification", table_name="items")
    op.drop_constraint("ck_items_classification_versions", "items", type_="check")
    op.drop_column("items", "classification_completed_version")
    op.drop_column("items", "classification_required_version")
