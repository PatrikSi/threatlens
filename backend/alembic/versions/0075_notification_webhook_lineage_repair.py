"""repair ambiguous legacy notification webhook lineage

Revision ID: 0075_notification_lineage_repair
Revises: 0074_ai_provider_receipts
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0075_notification_lineage_repair"
down_revision = "0074_ai_provider_receipts"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = "00000000-0000-4000-8000-000000000202"
_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    "[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def upgrade() -> None:
    bind = op.get_bind()
    revision = _require_disabled_data_policy(bind, operation="repair")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                handling_labels,
                feeds,
                items,
                notification_webhook_deliveries,
                integration_events,
                integration_deliveries,
                data_access_envelopes,
                data_access_envelope_sources,
                data_access_envelope_labels
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )
    quarantine_active = bind.scalar(
        sa.text(
            "SELECT is_active FROM handling_labels WHERE id = :quarantine"
        ),
        {"quarantine": _QUARANTINE_LABEL_ID},
    )
    if quarantine_active is not True:
        raise RuntimeError(
            "Cannot repair notification webhook lineage because the quarantine "
            "handling label is missing or inactive."
        )

    _verify_repairable_roots(bind)
    _verify_repair_identity_space(bind)
    _repair_sources(bind, policy_revision=revision)
    _rebuild_aggregates(bind, policy_revision=revision)
    _verify_repaired_sources(bind, policy_revision=revision)
    _verify_aggregate_invariants(bind)
    _correct_legacy_delivery_references(bind)


def downgrade() -> None:
    # This migration repairs data in place and adds no schema. Restoring the
    # ambiguous pre-repair attribution would widen access, so downgrade keeps
    # the quarantined lineage intact and only moves the Alembic revision marker.
    _require_disabled_data_policy(op.get_bind(), operation="downgrade")


def _require_disabled_data_policy(bind, *, operation: str) -> int:
    state = (
        bind.execute(
            sa.text(
                "SELECT mode, coverage_version, revision "
                "FROM data_policy_state WHERE id = 1 FOR UPDATE"
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        raise RuntimeError(
            f"Cannot {operation} notification webhook lineage because data "
            "policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} notification webhook lineage while data policy "
            "audit or enforcement is active. Disable data policy first."
        )
    return int(state["revision"])


def _repair_tree_cte() -> str:
    return f"""
        conflicting_legacy_deliveries AS (
            SELECT legacy.id
            FROM notification_webhook_deliveries AS legacy
            LEFT JOIN items AS item ON item.id = legacy.item_id
            WHERE legacy.feed_id IS NOT NULL
              AND (
                    (
                        legacy.item_id IS NOT NULL
                        AND (
                            item.id IS NULL
                            OR item.feed_id <> legacy.feed_id
                        )
                    )
                    OR (
                        legacy.item_id IS NULL
                        AND legacy.event_type_snapshot <> 'feed_failing'
                    )
              )
        ),
        conflicting_event_envelopes AS (
            SELECT DISTINCT envelope.id AS envelope_id
            FROM data_access_envelopes AS envelope
            JOIN integration_events AS event
              ON envelope.resource_type = 'integration_event'
             AND envelope.resource_id = event.id
             AND event.source_type = 'notification_webhook_delivery'
            JOIN conflicting_legacy_deliveries AS legacy
              ON legacy.id = CASE
                  WHEN event.source_id ~ '{_UUID_PATTERN}'
                  THEN event.source_id::uuid
                  ELSE NULL
              END
        ),
        conflicting_delivery_envelopes AS (
            SELECT DISTINCT envelope.id AS envelope_id
            FROM data_access_envelopes AS envelope
            JOIN integration_deliveries AS delivery
              ON envelope.resource_type = 'integration_delivery'
             AND envelope.resource_id = delivery.id
             AND delivery.event_id IS NULL
             AND delivery.source_delivery_id IS NULL
            JOIN conflicting_legacy_deliveries AS legacy
              ON legacy.id = CASE
                  WHEN jsonb_typeof(delivery.payload_json::jsonb) = 'object'
                   AND COALESCE(
                           delivery.payload_json::jsonb
                               ->>'legacy_webhook_delivery_id',
                           ''
                       ) ~ '{_UUID_PATTERN}'
                  THEN (
                      delivery.payload_json::jsonb
                          ->>'legacy_webhook_delivery_id'
                  )::uuid
                  ELSE NULL
              END
        ),
        conflicting_root_envelopes AS (
            SELECT envelope_id FROM conflicting_event_envelopes
            UNION
            SELECT envelope_id FROM conflicting_delivery_envelopes
        ),
        repair_tree (
            id, envelope_id, source_parent_id, root_id, root_source_id,
            root_captured_at
        ) AS (
            SELECT source.id,
                   source.envelope_id,
                   source.source_parent_id,
                   source.id,
                   source.source_id,
                   source.captured_at
            FROM data_access_envelope_sources AS source
            JOIN conflicting_root_envelopes AS affected
              ON affected.envelope_id = source.envelope_id
            WHERE source.source_parent_id IS NULL
            UNION
            SELECT child.id,
                   child.envelope_id,
                   child.source_parent_id,
                   parent.root_id,
                   parent.root_source_id,
                   parent.root_captured_at
            FROM data_access_envelope_sources AS child
            JOIN repair_tree AS parent ON child.source_parent_id = parent.id
        )
    """


def _verify_repairable_roots(bind) -> None:
    missing = int(
        bind.scalar(
            sa.text(
                f"""
                WITH RECURSIVE {_repair_tree_cte()}
                SELECT count(*)
                FROM conflicting_root_envelopes AS affected
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM repair_tree AS source
                    WHERE source.envelope_id = affected.envelope_id
                      AND source.source_parent_id IS NULL
                )
                """
            )
        )
        or 0
    )
    if missing:
        raise RuntimeError(
            "Cannot repair notification webhook lineage because one or more "
            "affected event envelopes have no normalized root source."
        )


def _verify_repair_identity_space(bind) -> None:
    conflicts = int(
        bind.scalar(
            sa.text(
                f"""
                WITH RECURSIVE {_repair_tree_cte()}
                SELECT count(*)
                FROM repair_tree AS repair
                JOIN data_access_envelope_sources AS existing
                  ON existing.envelope_id = repair.envelope_id
                 AND existing.source_type = 'unresolved'
                 AND existing.source_id = repair.root_source_id
                 AND existing.source_version =
                     'repair:0075:' || repair.root_id::text
                 AND existing.source_parent_id IS NOT DISTINCT FROM
                     repair.source_parent_id
                 AND existing.id <> repair.id
                """
            )
        )
        or 0
    )
    if conflicts:
        raise RuntimeError(
            "Cannot repair notification webhook lineage because a repaired "
            "source identity is already occupied."
        )


def _repair_sources(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            f"""
            WITH RECURSIVE {_repair_tree_cte()}
            UPDATE data_access_envelope_sources AS source
            SET source_type = 'unresolved',
                source_id = repair.root_source_id,
                source_version = 'repair:0075:' || repair.root_id::text,
                source_feed_id = NULL,
                handling_label_id = :quarantine,
                captured_policy_revision = :policy_revision,
                source_digest = NULL,
                captured_at = repair.root_captured_at
            FROM repair_tree AS repair
            WHERE source.id = repair.id
            """
        ),
        {
            "policy_revision": policy_revision,
            "quarantine": _QUARANTINE_LABEL_ID,
        },
    )


def _rebuild_aggregates(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            f"""
            WITH RECURSIVE {_repair_tree_cte()},
            affected_envelopes AS (
                SELECT DISTINCT envelope_id FROM repair_tree
            )
            DELETE FROM data_access_envelope_labels AS label
            USING affected_envelopes AS affected
            WHERE label.envelope_id = affected.envelope_id
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            WITH RECURSIVE {_repair_tree_cte()},
            affected_envelopes AS (
                SELECT DISTINCT envelope_id FROM repair_tree
            )
            INSERT INTO data_access_envelope_labels
                (envelope_id, label_id, source_count)
            SELECT source.envelope_id,
                   source.handling_label_id,
                   count(*)::integer
            FROM data_access_envelope_sources AS source
            JOIN affected_envelopes AS affected
              ON affected.envelope_id = source.envelope_id
            GROUP BY source.envelope_id, source.handling_label_id
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            WITH RECURSIVE {_repair_tree_cte()},
            affected_envelopes AS (
                SELECT DISTINCT envelope_id FROM repair_tree
            ),
            totals AS (
                SELECT source.envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources AS source
                JOIN affected_envelopes AS affected
                  ON affected.envelope_id = source.envelope_id
                GROUP BY source.envelope_id
            )
            UPDATE data_access_envelopes AS envelope
            SET source_count = totals.source_count,
                policy_revision = :policy_revision,
                updated_at = now()
            FROM totals
            WHERE envelope.id = totals.envelope_id
            """
        ),
        {"policy_revision": policy_revision},
    )


def _verify_repaired_sources(bind, *, policy_revision: int) -> None:
    inconsistent = int(
        bind.scalar(
            sa.text(
                f"""
                WITH RECURSIVE {_repair_tree_cte()}
                SELECT count(*)
                FROM repair_tree AS repair
                JOIN data_access_envelope_sources AS source
                  ON source.id = repair.id
                WHERE source.source_type <> 'unresolved'
                   OR source.source_id <> repair.root_source_id
                   OR source.source_version <>
                      'repair:0075:' || repair.root_id::text
                   OR source.source_feed_id IS NOT NULL
                   OR source.handling_label_id <> :quarantine
                   OR source.captured_policy_revision <> :policy_revision
                   OR source.source_digest IS NOT NULL
                   OR source.captured_at IS DISTINCT FROM repair.root_captured_at
                """
            ),
            {
                "policy_revision": policy_revision,
                "quarantine": _QUARANTINE_LABEL_ID,
            },
        )
        or 0
    )
    if inconsistent:
        raise RuntimeError(
            "Notification webhook lineage repair left one or more source rows "
            "in an unsafe state."
        )


def _verify_aggregate_invariants(bind) -> None:
    inconsistent = int(
        bind.scalar(
            sa.text(
                f"""
                WITH RECURSIVE {_repair_tree_cte()},
                affected_envelopes AS (
                    SELECT DISTINCT envelope_id FROM repair_tree
                ),
                source_labels AS (
                    SELECT source.envelope_id,
                           source.handling_label_id AS label_id,
                           count(*)::integer AS source_count
                    FROM data_access_envelope_sources AS source
                    JOIN affected_envelopes AS affected
                      ON affected.envelope_id = source.envelope_id
                    GROUP BY source.envelope_id, source.handling_label_id
                ),
                label_mismatches AS (
                    SELECT COALESCE(source.envelope_id, label.envelope_id)
                               AS envelope_id
                    FROM source_labels AS source
                    FULL OUTER JOIN data_access_envelope_labels AS label
                      ON label.envelope_id = source.envelope_id
                     AND label.label_id = source.label_id
                    JOIN affected_envelopes AS affected
                      ON affected.envelope_id =
                         COALESCE(source.envelope_id, label.envelope_id)
                    WHERE source.source_count IS DISTINCT FROM label.source_count
                )
                SELECT count(*)
                FROM data_access_envelopes AS envelope
                JOIN affected_envelopes AS affected
                  ON affected.envelope_id = envelope.id
                WHERE envelope.source_count <> (
                        SELECT count(*)
                        FROM data_access_envelope_sources AS source
                        WHERE source.envelope_id = envelope.id
                    )
                   OR envelope.policy_revision < COALESCE((
                        SELECT max(source.captured_policy_revision)
                        FROM data_access_envelope_sources AS source
                        WHERE source.envelope_id = envelope.id
                    ), 1)
                   OR EXISTS (
                        SELECT 1 FROM label_mismatches AS mismatch
                        WHERE mismatch.envelope_id = envelope.id
                    )
                """
            )
        )
        or 0
    )
    if inconsistent:
        raise RuntimeError(
            "Notification webhook lineage repair produced inconsistent envelope "
            "aggregates."
        )


def _correct_legacy_delivery_references(bind) -> None:
    # Do this only after every envelope verification above: changing feed_id
    # removes the rows from the mismatch CTE used to prove the recursive repair.
    bind.execute(
        sa.text(
            """
            UPDATE notification_webhook_deliveries AS legacy
            SET feed_id = item.feed_id
            FROM items AS item
            WHERE legacy.item_id = item.id
              AND legacy.feed_id IS NOT NULL
              AND legacy.feed_id <> item.feed_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE notification_webhook_deliveries AS legacy
            SET feed_id = NULL
            WHERE legacy.feed_id IS NOT NULL
              AND (
                    (
                        legacy.item_id IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM items AS item
                            WHERE item.id = legacy.item_id
                        )
                    )
                    OR (
                        legacy.item_id IS NULL
                        AND legacy.event_type_snapshot <> 'feed_failing'
                    )
              )
            """
        )
    )
