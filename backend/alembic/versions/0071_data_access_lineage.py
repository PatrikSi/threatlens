"""add normalized data-access envelope lineage

Revision ID: 0071_data_access_lineage
Revises: 0070_data_access_envelopes
Create Date: 2026-08-30
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = "0071_data_access_lineage"
down_revision = "0070_data_access_envelopes"
branch_labels = None
depends_on = None


UNRESTRICTED_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000201")
QUARANTINE_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")
_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_SHA256_PATTERN = "^[0-9A-Fa-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "data_access_envelope_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("envelope_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("source_feed_id", sa.Uuid(), nullable=True),
        sa.Column("source_parent_id", sa.Uuid(), nullable=True),
        sa.Column("handling_label_id", sa.Uuid(), nullable=False),
        sa.Column("captured_policy_revision", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_type = lower(source_type) AND "
            "source_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_data_access_envelope_sources_type",
        ),
        sa.CheckConstraint(
            "source_id = btrim(source_id) AND length(source_id) BETWEEN 1 AND 512 "
            "AND source_id !~ '[[:cntrl:]]'",
            name="ck_data_access_envelope_sources_id",
        ),
        sa.CheckConstraint(
            "source_version = btrim(source_version) "
            "AND length(source_version) BETWEEN 1 AND 128 "
            "AND source_version !~ '[[:cntrl:]]'",
            name="ck_data_access_envelope_sources_version",
        ),
        sa.CheckConstraint(
            "captured_policy_revision >= 1",
            name="ck_data_access_envelope_sources_policy_revision",
        ),
        sa.CheckConstraint(
            "source_digest IS NULL OR source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_data_access_envelope_sources_digest",
        ),
        sa.ForeignKeyConstraint(
            ["envelope_id"], ["data_access_envelopes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_feed_id"], ["feeds.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_parent_id"],
            ["data_access_envelope_sources.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["handling_label_id"], ["handling_labels.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "envelope_id",
            "source_type",
            "source_id",
            "source_version",
            "source_parent_id",
            name="uq_data_access_envelope_sources_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_data_access_envelope_sources_feed_envelope",
        "data_access_envelope_sources",
        ["source_feed_id", "envelope_id"],
    )
    op.create_index(
        "ix_data_access_envelope_sources_label_envelope",
        "data_access_envelope_sources",
        ["handling_label_id", "envelope_id"],
    )
    op.create_index(
        "ix_data_access_envelope_sources_parent_source",
        "data_access_envelope_sources",
        ["source_parent_id", "envelope_id"],
    )

    bind = op.get_bind()
    _lock_lineage_inputs(bind)
    _delete_orphan_envelopes(bind)
    _verify_supported_envelopes(bind)
    _backfill_reports(bind)
    _backfill_daily_briefs(bind)
    _backfill_alert_occurrences(bind)
    _backfill_investigations(bind)
    _backfill_integration_events(bind)
    _backfill_integration_deliveries(bind)
    _verify_complete_lineage(bind)
    _rebuild_aggregates(bind)
    _verify_complete_lineage(bind)
    _verify_aggregate_invariants(bind)


def downgrade() -> None:
    op.drop_index(
        "ix_data_access_envelope_sources_parent_source",
        table_name="data_access_envelope_sources",
    )
    op.drop_index(
        "ix_data_access_envelope_sources_label_envelope",
        table_name="data_access_envelope_sources",
    )
    op.drop_index(
        "ix_data_access_envelope_sources_feed_envelope",
        table_name="data_access_envelope_sources",
    )
    op.drop_table("data_access_envelope_sources")


def _insert_sources(bind, source_query: str, parameters: dict | None = None):
    return bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_sources (
                id, envelope_id, source_type, source_id, source_version,
                source_feed_id, source_parent_id, handling_label_id,
                captured_policy_revision, source_digest, captured_at
            )
            SELECT md5(
                       source.envelope_id::text || ':' || source.source_type ||
                       ':' || source.source_id || ':' || source.source_version ||
                       ':' || COALESCE(source.source_parent_id::text, 'direct')
                   )::uuid,
                   source.envelope_id, source.source_type, source.source_id,
                   source.source_version, source.source_feed_id,
                   source.source_parent_id, source.handling_label_id,
                   source.captured_policy_revision, source.source_digest,
                   source.captured_at
            FROM (
            """
            + source_query
            + """
            ) AS source
            """
        ),
        parameters or {},
    )


def _lock_lineage_inputs(bind) -> None:
    # The 0070 application does not dual-write normalized lineage. Drain its
    # writers for this transaction so the backfill and aggregate cutover are atomic.
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                ai_daily_brief_source_items,
                ai_daily_briefs,
                alert_occurrences,
                data_access_envelope_labels,
                data_access_envelopes,
                data_policy_state,
                feeds,
                handling_labels,
                integration_deliveries,
                integration_events,
                investigation_evidence,
                investigations,
                item_iocs,
                items,
                notification_webhook_deliveries,
                report_source_items,
                reports
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )


def _delete_orphan_envelopes(bind) -> None:
    resource_tables = (
        ("report", "reports"),
        ("ai_daily_brief", "ai_daily_briefs"),
        ("investigation", "investigations"),
        ("alert_occurrence", "alert_occurrences"),
        ("integration_event", "integration_events"),
        ("integration_delivery", "integration_deliveries"),
    )
    for resource_type, table_name in resource_tables:
        bind.execute(
            sa.text(
                f"""
                DELETE FROM data_access_envelopes AS envelope
                WHERE envelope.resource_type = :resource_type
                  AND NOT EXISTS (
                      SELECT 1 FROM {table_name} AS resource
                      WHERE resource.id = envelope.resource_id
                  )
                """
            ),
            {"resource_type": resource_type},
        )


def _verify_supported_envelopes(bind) -> None:
    unsupported = bind.scalar(
        sa.text(
            """
            SELECT count(*)
            FROM data_access_envelopes
            WHERE resource_type NOT IN (
                'report', 'ai_daily_brief', 'investigation',
                'alert_occurrence', 'integration_event', 'integration_delivery'
            )
            """
        )
    )
    if unsupported:
        raise RuntimeError(
            f"Data access lineage migration found {unsupported} unsupported envelopes"
        )


def _active_feed_label_sql(feed_alias: str, label_alias: str) -> str:
    return (
        f"CASE WHEN {label_alias}.is_active "
        f"THEN {feed_alias}.handling_label_id ELSE :quarantine END"
    )


def _backfill_reports(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               source.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               source.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN report_source_items AS source
          ON envelope.resource_type = 'report'
         AND envelope.resource_id = source.report_id
        JOIN items AS item ON item.id = source.item_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               COALESCE(source.item_id::text, source.id::text) AS source_id,
               source.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               source.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN report_source_items AS source
          ON envelope.resource_type = 'report'
         AND envelope.resource_id = source.report_id
        LEFT JOIN items AS item ON item.id = source.item_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1 AND item.id IS NULL
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _fill_source_free_envelopes(bind, resource_type="report", table_name="reports")


def _backfill_daily_briefs(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               source.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               source.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN ai_daily_brief_source_items AS source
          ON envelope.resource_type = 'ai_daily_brief'
         AND envelope.resource_id = source.daily_brief_id
        JOIN items AS item ON item.id = source.item_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               COALESCE(source.item_id::text, source.id::text) AS source_id,
               source.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               source.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN ai_daily_brief_source_items AS source
          ON envelope.resource_type = 'ai_daily_brief'
         AND envelope.resource_id = source.daily_brief_id
        LEFT JOIN items AS item ON item.id = source.item_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1 AND item.id IS NULL
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _fill_source_free_envelopes(
        bind,
        resource_type="ai_daily_brief",
        table_name="ai_daily_briefs",
    )


def _backfill_alert_occurrences(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               occurrence.item_id_snapshot::text AS source_id,
               occurrence.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN occurrence.item_content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(occurrence.item_content_hash) ELSE NULL END AS source_digest,
               occurrence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN alert_occurrences AS occurrence
          ON envelope.resource_type = 'alert_occurrence'
         AND envelope.resource_id = occurrence.id
        JOIN items AS item ON item.id = occurrence.item_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               occurrence.item_id_snapshot::text AS source_id,
               occurrence.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               occurrence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN alert_occurrences AS occurrence
          ON envelope.resource_type = 'alert_occurrence'
         AND envelope.resource_id = occurrence.id
        LEFT JOIN items AS item ON item.id = occurrence.item_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1 AND item.id IS NULL
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _backfill_investigations(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               evidence.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               evidence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN investigation_evidence AS evidence
          ON envelope.resource_type = 'investigation'
         AND envelope.resource_id = evidence.investigation_id
         AND evidence.source_type = 'item'
        JOIN items AS item ON item.id = evidence.source_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               evidence.source_id::text AS source_id,
               evidence.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               evidence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN investigation_evidence AS evidence
          ON envelope.resource_type = 'investigation'
         AND envelope.resource_id = evidence.investigation_id
         AND evidence.source_type = 'item'
        LEFT JOIN items AS item ON item.id = evidence.source_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1 AND item.id IS NULL
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _backfill_investigation_iocs(bind)
    for source_type, resource_type in (
        ("report", "report"),
        ("alert_occurrence", "alert_occurrence"),
    ):
        _copy_investigation_nested_sources(
            bind,
            source_type=source_type,
            resource_type=resource_type,
        )
    _fill_source_free_envelopes(
        bind,
        resource_type="investigation",
        table_name="investigations",
    )


def _backfill_investigation_iocs(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               evidence.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               evidence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN investigation_evidence AS evidence
          ON envelope.resource_type = 'investigation'
         AND envelope.resource_id = evidence.investigation_id
         AND evidence.source_type = 'ioc'
        JOIN item_iocs AS item_ioc ON item_ioc.ioc_id = evidence.source_id
        JOIN items AS item ON item.id = item_ioc.item_id
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               evidence.source_id::text AS source_id,
               evidence.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               evidence.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN investigation_evidence AS evidence
          ON envelope.resource_type = 'investigation'
         AND envelope.resource_id = evidence.investigation_id
         AND evidence.source_type = 'ioc'
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
          AND NOT EXISTS (
              SELECT 1
              FROM item_iocs AS item_ioc
              JOIN items AS item ON item.id = item_ioc.item_id
              JOIN feeds AS feed ON feed.id = item.feed_id
              WHERE item_ioc.ioc_id = evidence.source_id
          )
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _copy_investigation_nested_sources(
    bind, *, source_type: str, resource_type: str
) -> None:
    _insert_sources(
        bind,
        """
        SELECT target.id AS envelope_id,
               source.source_type,
               source.source_id,
               source.source_version,
               source.source_feed_id,
               source.id AS source_parent_id,
               source.handling_label_id,
               source.captured_policy_revision,
               source.source_digest,
               source.captured_at
        FROM data_access_envelopes AS target
        JOIN investigation_evidence AS evidence
          ON target.resource_type = 'investigation'
         AND target.resource_id = evidence.investigation_id
         AND evidence.source_type = :source_type
        JOIN data_access_envelopes AS parent
          ON parent.resource_type = :resource_type
         AND parent.resource_id = evidence.source_id
        JOIN data_access_envelope_sources AS source
          ON source.envelope_id = parent.id
        """,
        {"source_type": source_type, "resource_type": resource_type},
    )
    _insert_sources(
        bind,
        """
        SELECT target.id AS envelope_id,
               'unresolved' AS source_type,
               evidence.source_id::text AS source_id,
               evidence.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               evidence.created_at AS captured_at
        FROM data_access_envelopes AS target
        JOIN investigation_evidence AS evidence
          ON target.resource_type = 'investigation'
         AND target.resource_id = evidence.investigation_id
         AND evidence.source_type = :source_type
        LEFT JOIN data_access_envelopes AS parent
          ON parent.resource_type = :resource_type
         AND parent.resource_id = evidence.source_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_sources AS source
              WHERE source.envelope_id = parent.id
          )
        """,
        {
            "source_type": source_type,
            "resource_type": resource_type,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )


def _backfill_integration_events(bind) -> None:
    _insert_event_item_sources(bind)
    _insert_event_feed_sources(bind)
    _copy_event_nested_sources(bind, source_type="report", resource_type="report")
    for source_type in ("ai_daily_brief", "daily_brief"):
        _copy_event_nested_sources(
            bind,
            source_type=source_type,
            resource_type="ai_daily_brief",
        )
    _insert_webhook_failure_sources(bind)
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               CASE WHEN event.source_type IN ('system', 'test', 'digest_window')
                    THEN 'system' ELSE 'unresolved' END AS source_type,
               COALESCE(NULLIF(btrim(event.source_id), ''), event.id::text) AS source_id,
               'event:' || event.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               CASE WHEN event.source_type IN ('system', 'test', 'digest_window')
                    THEN :unrestricted ELSE :quarantine END AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               event.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_events AS event
          ON envelope.resource_type = 'integration_event'
         AND envelope.resource_id = event.id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_sources AS source
              WHERE source.envelope_id = envelope.id
          )
        """,
        {
            "unrestricted": UNRESTRICTED_LABEL_ID,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )


def _insert_event_item_sources(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               'event:' || event.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               event.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_events AS event
          ON envelope.resource_type = 'integration_event'
         AND envelope.resource_id = event.id
         AND event.source_type = 'item'
        JOIN items AS item
          ON event.source_id ~ '{_UUID_PATTERN}'
         AND item.id = event.source_id::uuid
        JOIN feeds AS feed ON feed.id = item.feed_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _insert_event_feed_sources(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'feed' AS source_type,
               feed.id::text AS source_id,
               'event:' || event.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               event.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_events AS event
          ON envelope.resource_type = 'integration_event'
         AND envelope.resource_id = event.id
         AND event.source_type = 'feed'
        JOIN feeds AS feed
          ON event.source_id ~ '{_UUID_PATTERN}'
         AND feed.id = event.source_id::uuid
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _copy_event_nested_sources(bind, *, source_type: str, resource_type: str) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT target.id AS envelope_id,
               source.source_type,
               source.source_id,
               source.source_version,
               source.source_feed_id,
               source.id AS source_parent_id,
               source.handling_label_id,
               source.captured_policy_revision,
               source.source_digest,
               source.captured_at
        FROM data_access_envelopes AS target
        JOIN integration_events AS event
          ON target.resource_type = 'integration_event'
         AND target.resource_id = event.id
         AND event.source_type = :source_type
        JOIN data_access_envelopes AS parent
          ON parent.resource_type = :resource_type
         AND event.source_id ~ '{_UUID_PATTERN}'
         AND parent.resource_id = event.source_id::uuid
        JOIN data_access_envelope_sources AS source
          ON source.envelope_id = parent.id
        """,
        {"source_type": source_type, "resource_type": resource_type},
    )


def _insert_webhook_failure_sources(bind) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'item' AS source_type,
               item.id::text AS source_id,
               'event:' || event.id::text AS source_version,
               COALESCE(delivery_feed.id, item_feed.id) AS source_feed_id,
               NULL::uuid AS source_parent_id,
               CASE WHEN delivery_feed.id IS NOT NULL
                    THEN {_active_feed_label_sql("delivery_feed", "delivery_label")}
                    ELSE {_active_feed_label_sql("item_feed", "item_label")}
               END AS handling_label_id,
               state.revision AS captured_policy_revision,
               CASE WHEN item.content_hash ~ '{_SHA256_PATTERN}'
                    THEN lower(item.content_hash) ELSE NULL END AS source_digest,
               event.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_events AS event
          ON envelope.resource_type = 'integration_event'
         AND envelope.resource_id = event.id
         AND event.source_type = 'notification_webhook_delivery'
        JOIN notification_webhook_deliveries AS delivery
          ON event.source_id ~ '{_UUID_PATTERN}'
         AND delivery.id = event.source_id::uuid
        JOIN items AS item ON item.id = delivery.item_id
        JOIN feeds AS item_feed ON item_feed.id = item.feed_id
        LEFT JOIN feeds AS delivery_feed ON delivery_feed.id = delivery.feed_id
        LEFT JOIN handling_labels AS delivery_label
          ON delivery_label.id = delivery_feed.handling_label_id
        LEFT JOIN handling_labels AS item_label
          ON item_label.id = item_feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'feed' AS source_type,
               feed.id::text AS source_id,
               'event:' || event.id::text AS source_version,
               feed.id AS source_feed_id,
               NULL::uuid AS source_parent_id,
               {_active_feed_label_sql("feed", "label")} AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               event.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_events AS event
          ON envelope.resource_type = 'integration_event'
         AND envelope.resource_id = event.id
         AND event.source_type = 'notification_webhook_delivery'
        JOIN notification_webhook_deliveries AS delivery
          ON event.source_id ~ '{_UUID_PATTERN}'
         AND delivery.id = event.source_id::uuid
        JOIN feeds AS feed ON feed.id = delivery.feed_id
        LEFT JOIN items AS item ON item.id = delivery.item_id
        LEFT JOIN handling_labels AS label ON label.id = feed.handling_label_id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1 AND item.id IS NULL
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _backfill_integration_deliveries(bind) -> None:
    _copy_delivery_parent_sources(bind, parent_kind="event")
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               CASE WHEN delivery.delivery_kind = 'test'
                          OR delivery.event_type LIKE '%test%'
                    THEN 'system' ELSE 'unresolved' END AS source_type,
               delivery.id::text AS source_id,
               'delivery:' || delivery.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               CASE WHEN delivery.delivery_kind = 'test'
                          OR delivery.event_type LIKE '%test%'
                    THEN :unrestricted ELSE :quarantine END AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               delivery.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_deliveries AS delivery
          ON envelope.resource_type = 'integration_delivery'
         AND envelope.resource_id = delivery.id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
          AND delivery.event_id IS NULL
          AND delivery.source_delivery_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_sources AS source
              WHERE source.envelope_id = envelope.id
          )
        """,
        {
            "unrestricted": UNRESTRICTED_LABEL_ID,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )
    while _copy_delivery_parent_sources(bind, parent_kind="delivery") > 0:
        pass
    _insert_sources(
        bind,
        """
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               delivery.id::text AS source_id,
               'delivery:' || delivery.id::text AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               delivery.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN integration_deliveries AS delivery
          ON envelope.resource_type = 'integration_delivery'
         AND envelope.resource_id = delivery.id
        CROSS JOIN data_policy_state AS state
        WHERE state.id = 1
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_sources AS source
              WHERE source.envelope_id = envelope.id
          )
        """,
        {"quarantine": QUARANTINE_LABEL_ID},
    )


def _copy_delivery_parent_sources(bind, *, parent_kind: str) -> int:
    if parent_kind == "event":
        join_sql = """
        JOIN data_access_envelopes AS parent
          ON parent.resource_type = 'integration_event'
         AND parent.resource_id = delivery.event_id
        """
    elif parent_kind == "delivery":
        join_sql = """
        JOIN data_access_envelopes AS parent
          ON parent.resource_type = 'integration_delivery'
         AND parent.resource_id = delivery.source_delivery_id
        """
    else:  # pragma: no cover - migration programming error
        raise ValueError(f"Unsupported delivery parent kind: {parent_kind}")
    result = _insert_sources(
        bind,
        f"""
        SELECT target.id AS envelope_id,
               source.source_type,
               source.source_id,
               source.source_version,
               source.source_feed_id,
               source.id AS source_parent_id,
               source.handling_label_id,
               source.captured_policy_revision,
               source.source_digest,
               source.captured_at
        FROM data_access_envelopes AS target
        JOIN integration_deliveries AS delivery
          ON target.resource_type = 'integration_delivery'
         AND target.resource_id = delivery.id
        {join_sql}
        JOIN data_access_envelope_sources AS source
          ON source.envelope_id = parent.id
        WHERE NOT EXISTS (
            SELECT 1 FROM data_access_envelope_sources AS existing
            WHERE existing.envelope_id = target.id
        )
        """,
    )
    return max(0, int(result.rowcount or 0))


def _fill_source_free_envelopes(bind, *, resource_type: str, table_name: str) -> None:
    _insert_sources(
        bind,
        f"""
        SELECT envelope.id AS envelope_id,
               'unresolved' AS source_type,
               envelope.resource_id::text AS source_id,
               'migration:0071' AS source_version,
               NULL::uuid AS source_feed_id,
               NULL::uuid AS source_parent_id,
               :quarantine AS handling_label_id,
               state.revision AS captured_policy_revision,
               NULL::text AS source_digest,
               resource.created_at AS captured_at
        FROM data_access_envelopes AS envelope
        JOIN {table_name} AS resource ON resource.id = envelope.resource_id
        CROSS JOIN data_policy_state AS state
        WHERE envelope.resource_type = :resource_type
          AND state.id = 1
          AND NOT EXISTS (
              SELECT 1 FROM data_access_envelope_sources AS source
              WHERE source.envelope_id = envelope.id
          )
        """,
        {
            "resource_type": resource_type,
            "quarantine": QUARANTINE_LABEL_ID,
        },
    )


def _verify_complete_lineage(bind) -> None:
    missing = bind.scalar(
        sa.text(
            """
            SELECT count(*)
            FROM data_access_envelopes AS envelope
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelope_sources AS source
                WHERE source.envelope_id = envelope.id
            )
            """
        )
    )
    if missing:
        raise RuntimeError(
            f"Data access lineage backfill left {missing} envelopes without sources"
        )


def _rebuild_aggregates(bind) -> None:
    bind.execute(sa.text("DELETE FROM data_access_envelope_labels"))
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_labels
                (envelope_id, label_id, source_count)
            SELECT envelope_id, handling_label_id, count(*)::integer
            FROM data_access_envelope_sources
            GROUP BY envelope_id, handling_label_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE data_access_envelopes AS envelope
            SET source_count = totals.source_count,
                policy_revision = state.revision,
                updated_at = now()
            FROM (
                SELECT envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources
                GROUP BY envelope_id
            ) AS totals,
            data_policy_state AS state
            WHERE envelope.id = totals.envelope_id AND state.id = 1
            """
        )
    )


def _verify_aggregate_invariants(bind) -> None:
    inconsistent = bind.scalar(
        sa.text(
            """
            WITH source_totals AS (
                SELECT envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources
                GROUP BY envelope_id
            ),
            label_totals AS (
                SELECT envelope_id, sum(source_count)::integer AS label_count
                FROM data_access_envelope_labels
                GROUP BY envelope_id
            ),
            source_labels AS (
                SELECT envelope_id, handling_label_id AS label_id,
                       count(*)::integer AS source_count
                FROM data_access_envelope_sources
                GROUP BY envelope_id, handling_label_id
            ),
            label_mismatches AS (
                SELECT COALESCE(source.envelope_id, label.envelope_id) AS envelope_id
                FROM source_labels AS source
                FULL OUTER JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = source.envelope_id
                 AND label.label_id = source.label_id
                WHERE source.source_count IS DISTINCT FROM label.source_count
            )
            SELECT count(*)
            FROM data_access_envelopes AS envelope
            LEFT JOIN source_totals AS sources ON sources.envelope_id = envelope.id
            LEFT JOIN label_totals AS labels ON labels.envelope_id = envelope.id
            WHERE envelope.source_count IS DISTINCT FROM sources.source_count
               OR sources.source_count IS DISTINCT FROM labels.label_count
               OR EXISTS (
                    SELECT 1 FROM label_mismatches AS mismatch
                    WHERE mismatch.envelope_id = envelope.id
               )
            """
        )
    )
    if inconsistent:
        raise RuntimeError(
            f"Data access lineage backfill produced {inconsistent} inconsistent envelopes"
        )
