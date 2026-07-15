"""repair feed-dependent GUID dedupe keys

Revision ID: 0039_guid_dedupe_repair
Revises: 0038_integration_credentials
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0039_guid_dedupe_repair"
down_revision = "0038_integration_credentials"
branch_labels = None
depends_on = None


_EXPECTED_GUID_KEY = "'guid:' || feed_id::text || ':' || btrim(source_guid)"
_STAGED_GUID_KEY = "'migration:0039:guid-dedupe:' || id::text"

_COLLISIONS = sa.text(
    f"""
    WITH item_keys AS (
        SELECT
            id,
            dedupe_key,
            CASE
                WHEN source_guid IS NOT NULL AND source_guid <> ''
                THEN {_EXPECTED_GUID_KEY}
            END AS expected_key
        FROM items
    ),
    repair_candidates AS (
        SELECT
            id,
            dedupe_key,
            expected_key,
            {_STAGED_GUID_KEY} AS staged_key
        FROM item_keys
        WHERE expected_key IS NOT NULL
          AND dedupe_key IS DISTINCT FROM expected_key
    ),
    final_keys AS (
        SELECT
            item_keys.id,
            COALESCE(repair_candidates.expected_key, item_keys.dedupe_key) AS final_key,
            repair_candidates.id IS NOT NULL AS needs_repair
        FROM item_keys
        LEFT JOIN repair_candidates ON repair_candidates.id = item_keys.id
    ),
    target_collisions AS (
        SELECT
            'target' AS collision_type,
            final_key AS collision_key,
            array_agg(id ORDER BY id) AS item_ids
        FROM final_keys
        GROUP BY final_key
        HAVING count(*) > 1 AND bool_or(needs_repair)
    ),
    staging_collisions AS (
        SELECT
            'staging' AS collision_type,
            repair_candidates.staged_key AS collision_key,
            ARRAY[repair_candidates.id] || array_agg(owner.id ORDER BY owner.id) AS item_ids
        FROM repair_candidates
        JOIN items AS owner
          ON owner.dedupe_key = repair_candidates.staged_key
         AND owner.id <> repair_candidates.id
        GROUP BY repair_candidates.id, repair_candidates.staged_key
    )
    SELECT collision_type, collision_key, item_ids
    FROM target_collisions
    UNION ALL
    SELECT collision_type, collision_key, item_ids
    FROM staging_collisions
    ORDER BY collision_type, collision_key
    """
)

_STAGE_REPAIRS = sa.text(
    f"""
    UPDATE items
    SET dedupe_key = {_STAGED_GUID_KEY}
    WHERE source_guid IS NOT NULL
      AND source_guid <> ''
      AND dedupe_key IS DISTINCT FROM ({_EXPECTED_GUID_KEY})
    """
)

_APPLY_REPAIRS = sa.text(
    f"""
    UPDATE items
    SET dedupe_key = {_EXPECTED_GUID_KEY}
    WHERE source_guid IS NOT NULL
      AND source_guid <> ''
      AND dedupe_key = ({_STAGED_GUID_KEY})
    """
)


def upgrade() -> None:
    _repair_guid_dedupe_keys(op.get_bind())


def downgrade() -> None:
    # Repaired keys are valid at 0038; restoring stale feed-dependent values would be destructive.
    pass


def _repair_guid_dedupe_keys(bind: sa.Connection) -> None:
    bind.execute(sa.text("LOCK TABLE items IN SHARE ROW EXCLUSIVE MODE"))
    collisions = bind.execute(_COLLISIONS).mappings().all()
    if collisions:
        details = "; ".join(
            f"{row['collision_type']} key {row['collision_key']!r} item_ids="
            + ",".join(str(item_id) for item_id in row["item_ids"])
            for row in collisions
        )
        raise RuntimeError(f"Cannot repair GUID dedupe keys because collisions exist: {details}")

    bind.execute(_STAGE_REPAIRS)
    bind.execute(_APPLY_REPAIRS)
