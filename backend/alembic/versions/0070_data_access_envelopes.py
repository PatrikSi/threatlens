"""add durable data-access envelopes and quarantine defaults

Revision ID: 0070_data_access_envelopes
Revises: 0069_data_policy_foundation
Create Date: 2026-08-30
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = "0070_data_access_envelopes"
down_revision = "0069_data_policy_foundation"
branch_labels = None
depends_on = None


UNRESTRICTED_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000201")
QUARANTINE_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")
ADMIN_ROLE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    "[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO handling_labels "
            "(id, key, name, description, color, is_unrestricted, "
            "is_system, is_active, revision) "
            "VALUES (:id, 'quarantine', 'Quarantine', "
            "'Safe default for newly created or provenance-incomplete intelligence.', "
            "'#DC2626', false, true, true, 1) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": QUARANTINE_LABEL_ID},
    )
    bind.execute(
        sa.text(
            "INSERT INTO data_policy_role_grants (label_id, role_id) "
            "VALUES (:label_id, :role_id) "
            "ON CONFLICT (label_id, role_id) DO NOTHING"
        ),
        {"label_id": QUARANTINE_LABEL_ID, "role_id": ADMIN_ROLE_ID},
    )
    op.alter_column(
        "feeds",
        "handling_label_id",
        existing_type=sa.Uuid(),
        nullable=False,
        server_default=str(QUARANTINE_LABEL_ID),
    )

    op.create_table(
        "data_access_envelopes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "resource_type = lower(resource_type) AND "
            "resource_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_data_access_envelopes_resource_type",
        ),
        sa.CheckConstraint(
            "source_count >= 0", name="ck_data_access_envelopes_source_count"
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="ck_data_access_envelopes_policy_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_type",
            "resource_id",
            name="uq_data_access_envelopes_resource",
        ),
    )
    op.create_table(
        "data_access_envelope_labels",
        sa.Column("envelope_id", sa.Uuid(), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "source_count >= 1",
            name="ck_data_access_envelope_labels_source_count",
        ),
        sa.ForeignKeyConstraint(
            ["envelope_id"], ["data_access_envelopes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["handling_labels.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("envelope_id", "label_id"),
    )
    op.create_index(
        "ix_data_access_envelope_labels_label",
        "data_access_envelope_labels",
        ["label_id", "envelope_id"],
    )

    for resource_type, table_name in (
        ("report", "reports"),
        ("ai_daily_brief", "ai_daily_briefs"),
        ("alert_occurrence", "alert_occurrences"),
        ("investigation", "investigations"),
        ("integration_event", "integration_events"),
        ("integration_delivery", "integration_deliveries"),
    ):
        _insert_envelopes(bind, resource_type=resource_type, table_name=table_name)

    _backfill_reports(bind)
    _backfill_daily_briefs(bind)
    _backfill_alert_occurrences(bind)
    _backfill_investigations(bind)
    _backfill_integration_events(bind)
    _backfill_integration_deliveries(bind)
    _refresh_source_counts(bind)


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index(
        "ix_data_access_envelope_labels_label",
        table_name="data_access_envelope_labels",
    )
    op.drop_table("data_access_envelope_labels")
    op.drop_table("data_access_envelopes")
    op.alter_column(
        "feeds",
        "handling_label_id",
        existing_type=sa.Uuid(),
        nullable=False,
        server_default=str(UNRESTRICTED_LABEL_ID),
    )
    bind.execute(
        sa.text(
            "UPDATE feeds SET handling_label_id = :unrestricted "
            "WHERE handling_label_id = :quarantine"
        ),
        {
            "unrestricted": UNRESTRICTED_LABEL_ID,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )
    bind.execute(
        sa.text(
            "DELETE FROM data_policy_role_grants WHERE label_id = :label_id"
        ),
        {"label_id": QUARANTINE_LABEL_ID},
    )
    bind.execute(
        sa.text("DELETE FROM handling_labels WHERE id = :label_id"),
        {"label_id": QUARANTINE_LABEL_ID},
    )


def _insert_envelopes(bind, *, resource_type: str, table_name: str) -> None:
    bind.execute(
        sa.text(
            f"INSERT INTO data_access_envelopes "
            f"(id, resource_type, resource_id, source_count, policy_revision) "
            f"SELECT md5(:resource_type || ':' || id::text)::uuid, "
            f":resource_type, id, 0, "
            f"(SELECT revision FROM data_policy_state WHERE id = 1) "
            f"FROM {table_name} "
            f"ON CONFLICT (resource_type, resource_id) DO NOTHING"
        ),
        {"resource_type": resource_type},
    )


def _upsert_labels(bind, statement: str, parameters: dict | None = None) -> None:
    bind.execute(
        sa.text(
            statement
            + " ON CONFLICT (envelope_id, label_id) DO UPDATE SET "
            + "source_count = data_access_envelope_labels.source_count + "
            + "EXCLUDED.source_count"
        ),
        parameters or {},
    )


def _backfill_reports(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id,
               COALESCE(feed.handling_label_id, :quarantine),
               count(*)
        FROM data_access_envelopes AS envelope
        JOIN report_source_items AS source
          ON envelope.resource_type = 'report'
         AND envelope.resource_id = source.report_id
        LEFT JOIN items AS item ON item.id = source.item_id
        LEFT JOIN feeds AS feed ON feed.id = item.feed_id
        GROUP BY envelope.id, COALESCE(feed.handling_label_id, :quarantine)
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _fill_empty_envelopes(bind, "report", UNRESTRICTED_LABEL_ID)


def _backfill_daily_briefs(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id,
               COALESCE(feed.handling_label_id, :quarantine),
               count(*)
        FROM data_access_envelopes AS envelope
        JOIN ai_daily_brief_source_items AS source
          ON envelope.resource_type = 'ai_daily_brief'
         AND envelope.resource_id = source.daily_brief_id
        LEFT JOIN items AS item ON item.id = source.item_id
        LEFT JOIN feeds AS feed ON feed.id = item.feed_id
        GROUP BY envelope.id, COALESCE(feed.handling_label_id, :quarantine)
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _fill_empty_envelopes(bind, "ai_daily_brief", UNRESTRICTED_LABEL_ID)


def _backfill_alert_occurrences(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id,
               COALESCE(feed.handling_label_id, :quarantine),
               1
        FROM data_access_envelopes AS envelope
        JOIN alert_occurrences AS occurrence
          ON envelope.resource_type = 'alert_occurrence'
         AND envelope.resource_id = occurrence.id
        LEFT JOIN items AS item ON item.id = occurrence.item_id
        LEFT JOIN feeds AS feed ON feed.id = item.feed_id
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _backfill_investigations(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id,
               COALESCE(feed.handling_label_id, :quarantine),
               count(*)
        FROM data_access_envelopes AS envelope
        JOIN investigation_evidence AS evidence
          ON envelope.resource_type = 'investigation'
         AND envelope.resource_id = evidence.investigation_id
         AND evidence.source_type = 'item'
        LEFT JOIN items AS item ON item.id = evidence.source_id
        LEFT JOIN feeds AS feed ON feed.id = item.feed_id
        GROUP BY envelope.id, COALESCE(feed.handling_label_id, :quarantine)
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    for source_type, resource_type in (
        ("report", "report"),
        ("alert_occurrence", "alert_occurrence"),
    ):
        _upsert_labels(
            bind,
            """
            INSERT INTO data_access_envelope_labels
                (envelope_id, label_id, source_count)
            SELECT target.id, source_label.label_id, sum(source_label.source_count)
            FROM data_access_envelopes AS target
            JOIN investigation_evidence AS evidence
              ON target.resource_type = 'investigation'
             AND target.resource_id = evidence.investigation_id
             AND evidence.source_type = :source_type
            JOIN data_access_envelopes AS source_envelope
              ON source_envelope.resource_type = :resource_type
             AND source_envelope.resource_id = evidence.source_id
            JOIN data_access_envelope_labels AS source_label
              ON source_label.envelope_id = source_envelope.id
            GROUP BY target.id, source_label.label_id
            """,
            {"source_type": source_type, "resource_type": resource_type},
        )
        _quarantine_unresolved_evidence(bind, source_type, resource_type)

    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id, feed.handling_label_id, count(*)
        FROM data_access_envelopes AS target
        JOIN investigation_evidence AS evidence
          ON target.resource_type = 'investigation'
         AND target.resource_id = evidence.investigation_id
         AND evidence.source_type = 'ioc'
        JOIN item_iocs AS item_ioc ON item_ioc.ioc_id = evidence.source_id
        JOIN items AS item ON item.id = item_ioc.item_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        GROUP BY target.id, feed.handling_label_id
        """,
    )
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id, :quarantine, count(*)
        FROM data_access_envelopes AS target
        JOIN investigation_evidence AS evidence
          ON target.resource_type = 'investigation'
         AND target.resource_id = evidence.investigation_id
         AND evidence.source_type = 'ioc'
        WHERE NOT EXISTS (
            SELECT 1 FROM item_iocs WHERE item_iocs.ioc_id = evidence.source_id
        )
        GROUP BY target.id
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _fill_empty_envelopes(bind, "investigation", UNRESTRICTED_LABEL_ID)


def _quarantine_unresolved_evidence(
    bind, source_type: str, resource_type: str
) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id, :quarantine, count(*)
        FROM data_access_envelopes AS target
        JOIN investigation_evidence AS evidence
          ON target.resource_type = 'investigation'
         AND target.resource_id = evidence.investigation_id
         AND evidence.source_type = :source_type
        WHERE NOT EXISTS (
            SELECT 1
            FROM data_access_envelopes AS source_envelope
            WHERE source_envelope.resource_type = :resource_type
              AND source_envelope.resource_id = evidence.source_id
        )
        GROUP BY target.id
        """,
        {
            "quarantine": QUARANTINE_LABEL_ID,
            "source_type": source_type,
            "resource_type": resource_type,
        },
    )


def _backfill_integration_events(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id, :unrestricted, 1
        FROM data_access_envelopes AS envelope
        WHERE envelope.resource_type = 'integration_event'
        """,
        {"unrestricted": UNRESTRICTED_LABEL_ID},
    )
    for source_type, resource_type in (
        ("report", "report"),
        ("ai_daily_brief", "ai_daily_brief"),
    ):
        _replace_event_labels_from_envelope(bind, source_type, resource_type)
    _replace_item_event_labels(bind)
    _replace_feed_event_labels(bind)
    _replace_webhook_failure_event_labels(bind)


def _replace_event_labels_from_envelope(
    bind, source_type: str, resource_type: str
) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS target, integration_events AS event
            WHERE label.envelope_id = target.id
              AND target.resource_type = 'integration_event'
              AND target.resource_id = event.id
              AND event.source_type = :source_type
            """
        ),
        {"source_type": source_type},
    )
    _upsert_labels(
        bind,
        f"""
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id,
               COALESCE(source_label.label_id, :quarantine),
               COALESCE(source_label.source_count, 1)
        FROM data_access_envelopes AS target
        JOIN integration_events AS event
          ON target.resource_type = 'integration_event'
         AND target.resource_id = event.id
         AND event.source_type = :source_type
        LEFT JOIN data_access_envelopes AS source_envelope
          ON source_envelope.resource_type = :resource_type
         AND source_envelope.resource_id = CASE
             WHEN event.source_id ~ '{_UUID_PATTERN}' THEN event.source_id::uuid
             ELSE NULL
         END
        LEFT JOIN data_access_envelope_labels AS source_label
          ON source_label.envelope_id = source_envelope.id
        """,
        {
            "source_type": source_type,
            "resource_type": resource_type,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )


def _replace_item_event_labels(bind) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS target, integration_events AS event
            WHERE label.envelope_id = target.id
              AND target.resource_type = 'integration_event'
              AND target.resource_id = event.id
              AND event.source_type = 'item'
            """
        )
    )
    _upsert_labels(
        bind,
        f"""
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id,
               COALESCE(feed.handling_label_id, :quarantine),
               1
        FROM data_access_envelopes AS target
        JOIN integration_events AS event
          ON target.resource_type = 'integration_event'
         AND target.resource_id = event.id
         AND event.source_type = 'item'
        LEFT JOIN items AS item
          ON item.id = CASE
              WHEN event.source_id ~ '{_UUID_PATTERN}' THEN event.source_id::uuid
              ELSE NULL
          END
        LEFT JOIN feeds AS feed ON feed.id = item.feed_id
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _replace_feed_event_labels(bind) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS target, integration_events AS event
            WHERE label.envelope_id = target.id
              AND target.resource_type = 'integration_event'
              AND target.resource_id = event.id
              AND event.source_type = 'feed'
            """
        )
    )
    _upsert_labels(
        bind,
        f"""
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id,
               COALESCE(feed.handling_label_id, :quarantine),
               1
        FROM data_access_envelopes AS target
        JOIN integration_events AS event
          ON target.resource_type = 'integration_event'
         AND target.resource_id = event.id
         AND event.source_type = 'feed'
        LEFT JOIN feeds AS feed
          ON feed.id = CASE
              WHEN event.source_id ~ '{_UUID_PATTERN}' THEN event.source_id::uuid
              ELSE NULL
          END
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _replace_webhook_failure_event_labels(bind) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS target, integration_events AS event
            WHERE label.envelope_id = target.id
              AND target.resource_type = 'integration_event'
              AND target.resource_id = event.id
              AND event.source_type = 'notification_webhook_delivery'
            """
        )
    )
    _upsert_labels(
        bind,
        f"""
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id,
               COALESCE(
                   delivery_feed.handling_label_id,
                   item_feed.handling_label_id,
                   :quarantine
               ),
               1
        FROM data_access_envelopes AS target
        JOIN integration_events AS event
          ON target.resource_type = 'integration_event'
         AND target.resource_id = event.id
         AND event.source_type = 'notification_webhook_delivery'
        LEFT JOIN notification_webhook_deliveries AS delivery
          ON delivery.id = CASE
              WHEN event.source_id ~ '{_UUID_PATTERN}' THEN event.source_id::uuid
              ELSE NULL
          END
        LEFT JOIN feeds AS delivery_feed ON delivery_feed.id = delivery.feed_id
        LEFT JOIN items AS item ON item.id = delivery.item_id
        LEFT JOIN feeds AS item_feed ON item_feed.id = item.feed_id
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _backfill_integration_deliveries(bind) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id, source_label.label_id, source_label.source_count
        FROM data_access_envelopes AS target
        JOIN integration_deliveries AS delivery
          ON target.resource_type = 'integration_delivery'
         AND target.resource_id = delivery.id
        JOIN data_access_envelopes AS source_envelope
          ON source_envelope.resource_type = 'integration_event'
         AND source_envelope.resource_id = delivery.event_id
        JOIN data_access_envelope_labels AS source_label
          ON source_label.envelope_id = source_envelope.id
        """,
    )
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT target.id,
               CASE WHEN delivery.event_id IS NULL
                    THEN :unrestricted ELSE :quarantine END,
               1
        FROM data_access_envelopes AS target
        JOIN integration_deliveries AS delivery
          ON target.resource_type = 'integration_delivery'
         AND target.resource_id = delivery.id
        WHERE NOT EXISTS (
            SELECT 1 FROM data_access_envelope_labels AS existing
            WHERE existing.envelope_id = target.id
        )
        """,
        {
            "unrestricted": UNRESTRICTED_LABEL_ID,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )


def _fill_empty_envelopes(bind, resource_type: str, label_id: uuid.UUID) -> None:
    _upsert_labels(
        bind,
        """
        INSERT INTO data_access_envelope_labels
            (envelope_id, label_id, source_count)
        SELECT envelope.id, :label_id, 1
        FROM data_access_envelopes AS envelope
        WHERE envelope.resource_type = :resource_type
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_labels AS existing
              WHERE existing.envelope_id = envelope.id
          )
        """,
        {"label_id": label_id, "resource_type": resource_type},
    )


def _refresh_source_counts(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE data_access_envelopes AS envelope
            SET source_count = totals.source_count,
                updated_at = now()
            FROM (
                SELECT envelope_id, sum(source_count)::integer AS source_count
                FROM data_access_envelope_labels
                GROUP BY envelope_id
            ) AS totals
            WHERE totals.envelope_id = envelope.id
            """
        )
    )
