"""Bound feed identity indexes without changing retained publisher identifiers."""

from alembic import op
import sqlalchemy as sa

revision = "0123_item_identity_indexes"
down_revision = "0122_mcp_delegation"
branch_labels = depends_on = None


def upgrade():
    # The fixed UTF8 conversion makes this independent of client encoding.
    op.execute(sa.text("""
        CREATE FUNCTION threatlens_item_identity_digest(value text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
          SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(value, 'UTF8')), 'hex')
        $$
    """))
    # Populate both generated columns in one table rewrite on larger installs.
    op.execute(sa.text("""
        ALTER TABLE items
        ADD COLUMN dedupe_digest varchar(64)
          GENERATED ALWAYS AS (threatlens_item_identity_digest(dedupe_key)) STORED NOT NULL,
        ADD COLUMN source_guid_digest varchar(64)
          GENERATED ALWAYS AS (threatlens_item_identity_digest(source_guid)) STORED
    """))
    op.drop_constraint("uq_items_dedupe_key", "items", type_="unique")
    op.create_unique_constraint("uq_items_dedupe_digest", "items", ["dedupe_digest"])
    op.drop_index("ix_items_feed_guid_unique_not_null", table_name="items")
    op.create_index("ix_items_feed_guid_unique_not_null", "items", ["feed_id", "source_guid_digest"],
                    unique=True, postgresql_where=sa.text("source_guid_digest IS NOT NULL"))
    for column in ("source_guid", "canonical_url"):
        op.drop_index(f"ix_items_{column}", table_name="items")
        op.create_index(f"ix_items_{column}", "items", [column], postgresql_using="hash")


def downgrade():
    # Old B-tree entries cannot represent arbitrary publisher identities. Do not
    # silently truncate evidence or drop rows to make an older schema fit.
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM items WHERE octet_length(dedupe_key) > 2000
          OR octet_length(source_guid) > 2000 OR octet_length(canonical_url) > 2000)
    """)):
        raise RuntimeError("Retained long item identities require digest indexes; keep the current schema")
    op.drop_constraint("uq_items_dedupe_digest", "items", type_="unique")
    op.create_unique_constraint("uq_items_dedupe_key", "items", ["dedupe_key"])
    op.drop_index("ix_items_feed_guid_unique_not_null", table_name="items")
    op.create_index("ix_items_feed_guid_unique_not_null", "items", ["feed_id", "source_guid"],
                    unique=True, postgresql_where=sa.text("source_guid IS NOT NULL"))
    for column in ("source_guid", "canonical_url"):
        op.drop_index(f"ix_items_{column}", table_name="items")
        op.create_index(f"ix_items_{column}", "items", [column])
    op.drop_column("items", "source_guid_digest")
    op.drop_column("items", "dedupe_digest")
    op.execute(sa.text("DROP FUNCTION threatlens_item_identity_digest(text)"))
