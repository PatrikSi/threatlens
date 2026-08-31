"""govern retained AI telemetry and operations history

Revision ID: 0078_ai_telemetry_policy
Revises: 0077_audit_policy_snapshots
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0078_ai_telemetry_policy"
down_revision = "0077_audit_policy_snapshots"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = "00000000-0000-4000-8000-000000000202"
_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_AUDIT_FENCE_FUNCTION = "threatlens_fence_audit_ai_policy_v1"
_AUDIT_FENCE_TRIGGER = "trg_audit_logs_fence_ai_policy_v1"
_AUDIT_CAPTURE_FUNCTION = "threatlens_capture_audit_ai_policy_v1"
_AUDIT_CAPTURE_TRIGGER = "trg_audit_logs_capture_ai_policy_v1"
_AUDIT_FEED_TAINT_FUNCTION = "threatlens_taint_audit_ai_policy_v1"
_AUDIT_FEED_TAINT_TRIGGER = "trg_feeds_taint_audit_ai_policy_v1"


def upgrade() -> None:
    bind = op.get_bind()
    policy_revision = _require_disabled_data_policy(bind, operation="migrate")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                handling_labels,
                feeds,
                items,
                ai_daily_briefs,
                reports,
                ai_task_runs,
                ai_task_events,
                ai_usage_events,
                audit_logs,
                data_access_envelopes,
                data_access_envelope_sources,
                data_access_envelope_labels
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )
    quarantine_active = bind.scalar(
        sa.text("SELECT is_active FROM handling_labels WHERE id = :quarantine"),
        {"quarantine": _QUARANTINE_LABEL_ID},
    )
    if quarantine_active is not True:
        raise RuntimeError(
            "Cannot migrate AI telemetry because the quarantine handling label "
            "is missing or inactive."
        )

    _add_ai_scope_columns()
    _create_audit_lineage_tables()
    _classify_legacy_rows(bind)
    _create_ai_envelopes(bind, policy_revision=policy_revision)
    _backfill_audit_lineage(bind)
    _install_audit_lineage_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _require_disabled_data_policy(bind, operation="downgrade")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                feeds,
                handling_labels,
                ai_task_runs,
                ai_usage_events,
                audit_logs,
                audit_log_data_access_labels,
                audit_log_data_access_feeds,
                data_access_envelopes,
                data_access_envelope_sources,
                data_access_envelope_labels
            IN ACCESS EXCLUSIVE MODE
            """
        )
    )
    _drop_audit_lineage_triggers(bind)
    bind.execute(
        sa.text(
            "DELETE FROM data_access_envelopes WHERE resource_type = 'ai_usage_event'"
        )
    )
    bind.execute(
        sa.text("DELETE FROM data_access_envelopes WHERE resource_type = 'ai_task_run'")
    )
    op.drop_index(
        "ix_audit_log_data_access_feeds_feed",
        table_name="audit_log_data_access_feeds",
    )
    op.drop_table("audit_log_data_access_feeds")
    op.drop_index(
        "ix_audit_log_data_access_labels_label",
        table_name="audit_log_data_access_labels",
    )
    op.drop_table("audit_log_data_access_labels")
    op.drop_index(
        "ix_ai_usage_events_task_run_snapshot",
        table_name="ai_usage_events",
    )
    op.drop_constraint(
        "ck_ai_usage_events_system_scope",
        "ai_usage_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_usage_events_data_access_scope",
        "ai_usage_events",
        type_="check",
    )
    op.drop_column("ai_usage_events", "data_access_scope")
    op.drop_column("ai_usage_events", "task_run_id_snapshot")
    op.drop_constraint(
        "ck_ai_task_runs_system_scope",
        "ai_task_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_task_runs_data_access_scope",
        "ai_task_runs",
        type_="check",
    )
    op.drop_column("ai_task_runs", "data_access_lineage_complete")
    op.drop_column("ai_task_runs", "data_access_scope")


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
            f"Cannot {operation} AI telemetry because data policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} AI telemetry while data policy audit or "
            "enforcement is active. Disable data policy first."
        )
    return int(state["revision"])


def _add_ai_scope_columns() -> None:
    op.add_column(
        "ai_task_runs",
        sa.Column(
            "data_access_scope",
            sa.String(length=16),
            nullable=False,
            server_default="governed",
        ),
    )
    op.add_column(
        "ai_task_runs",
        sa.Column(
            "data_access_lineage_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_ai_task_runs_data_access_scope",
        "ai_task_runs",
        "data_access_scope IN ('system', 'governed')",
    )
    op.create_check_constraint(
        "ck_ai_task_runs_system_scope",
        "ai_task_runs",
        "data_access_scope <> 'system' OR "
        "(task_type = 'connection_test' AND data_access_lineage_complete "
        "AND item_id IS NULL AND daily_brief_id IS NULL "
        "AND report_id IS NULL AND parent_run_id IS NULL)",
    )
    op.add_column(
        "ai_usage_events",
        sa.Column("task_run_id_snapshot", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "ai_usage_events",
        sa.Column(
            "data_access_scope",
            sa.String(length=16),
            nullable=False,
            server_default="governed",
        ),
    )
    op.create_check_constraint(
        "ck_ai_usage_events_data_access_scope",
        "ai_usage_events",
        "data_access_scope IN ('system', 'governed')",
    )
    op.create_check_constraint(
        "ck_ai_usage_events_system_scope",
        "ai_usage_events",
        "data_access_scope <> 'system' OR "
        "(feature_type = 'connection_test' AND item_id IS NULL "
        "AND daily_brief_id IS NULL AND report_id IS NULL)",
    )
    op.create_index(
        "ix_ai_usage_events_task_run_snapshot",
        "ai_usage_events",
        ["task_run_id_snapshot"],
    )


def _create_audit_lineage_tables() -> None:
    op.create_table(
        "audit_log_data_access_labels",
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_log_id"], ["audit_logs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["handling_labels.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("audit_log_id", "label_id"),
    )
    op.create_index(
        "ix_audit_log_data_access_labels_label",
        "audit_log_data_access_labels",
        ["label_id", "audit_log_id"],
    )
    op.create_table(
        "audit_log_data_access_feeds",
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_feed_id_snapshot",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"], ["audit_logs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("audit_log_id", "source_feed_id_snapshot"),
    )
    op.create_index(
        "ix_audit_log_data_access_feeds_feed",
        "audit_log_data_access_feeds",
        ["source_feed_id_snapshot", "audit_log_id"],
    )


def _classify_legacy_rows(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE ai_task_runs
            SET data_access_scope = 'system',
                data_access_lineage_complete = true
            WHERE task_type = 'connection_test'
              AND item_id IS NULL
              AND daily_brief_id IS NULL
              AND report_id IS NULL
              AND parent_run_id IS NULL
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE ai_usage_events
            SET data_access_scope = 'system'
            WHERE feature_type = 'connection_test'
              AND item_id IS NULL
              AND daily_brief_id IS NULL
              AND report_id IS NULL
            """
        )
    )


def _create_ai_envelopes(bind, *, policy_revision: int) -> None:
    for resource_type, table_name in (
        ("ai_task_run", "ai_task_runs"),
        ("ai_usage_event", "ai_usage_events"),
    ):
        bind.execute(
            sa.text(
                f"""
                INSERT INTO data_access_envelopes (
                    id, resource_type, resource_id, source_count,
                    policy_revision, created_at, updated_at
                )
                SELECT md5('0078:{resource_type}:' || resource.id::text)::uuid,
                       :resource_type, resource.id, 0, :policy_revision,
                       resource.created_at, now()
                FROM {table_name} AS resource
                WHERE resource.data_access_scope = 'governed'
                ON CONFLICT (resource_type, resource_id) DO NOTHING
                """
            ),
            {"policy_revision": policy_revision, "resource_type": resource_type},
        )

    _insert_direct_item_sources(bind, policy_revision=policy_revision)
    _insert_copied_resource_sources(bind, policy_revision=policy_revision)
    _insert_unresolved_sources(bind, policy_revision=policy_revision)
    _rebuild_ai_envelopes(bind, policy_revision=policy_revision)
    bind.execute(
        sa.text(
            """
            UPDATE ai_task_runs
            SET data_access_lineage_complete = true
            WHERE data_access_scope = 'governed'
            """
        )
    )


def _insert_direct_item_sources(bind, *, policy_revision: int) -> None:
    for resource_type, table_name in (
        ("ai_task_run", "ai_task_runs"),
        ("ai_usage_event", "ai_usage_events"),
    ):
        bind.execute(
            sa.text(
                f"""
                INSERT INTO data_access_envelope_sources (
                    id, envelope_id, source_type, source_id, source_version,
                    source_feed_id, source_parent_id, handling_label_id,
                    captured_policy_revision, source_digest, captured_at
                )
                SELECT md5(
                           '0078:{resource_type}:item:' || resource.id::text
                       )::uuid,
                       envelope.id, 'item', item.id::text,
                       'migrate:0078:item', item.feed_id, NULL,
                       feed.handling_label_id, :policy_revision, NULL,
                       resource.created_at
                FROM {table_name} AS resource
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = :resource_type
                 AND envelope.resource_id = resource.id
                JOIN items AS item ON item.id = resource.item_id
                JOIN feeds AS feed ON feed.id = item.feed_id
                WHERE resource.data_access_scope = 'governed'
                ON CONFLICT DO NOTHING
                """
            ),
            {"policy_revision": policy_revision, "resource_type": resource_type},
        )


def _insert_copied_resource_sources(bind, *, policy_revision: int) -> None:
    for target_type, table_name in (
        ("ai_task_run", "ai_task_runs"),
        ("ai_usage_event", "ai_usage_events"),
    ):
        for id_column, source_type in (
            ("daily_brief_id", "ai_daily_brief"),
            ("report_id", "report"),
        ):
            bind.execute(
                sa.text(
                    f"""
                    INSERT INTO data_access_envelope_sources (
                        id, envelope_id, source_type, source_id,
                        source_version, source_feed_id, source_parent_id,
                        handling_label_id, captured_policy_revision,
                        source_digest, captured_at
                    )
                    SELECT md5(
                               '0078:{target_type}:' || target.id::text || ':' ||
                               source.id::text
                           )::uuid,
                           target_envelope.id, source.source_type,
                           source.source_id,
                           'migrate:0078:' || source.id::text,
                           source.source_feed_id, NULL,
                           source.handling_label_id,
                           LEAST(
                               :policy_revision,
                               source.captured_policy_revision
                           ),
                           source.source_digest, source.captured_at
                    FROM {table_name} AS target
                    JOIN data_access_envelopes AS target_envelope
                      ON target_envelope.resource_type = :target_type
                     AND target_envelope.resource_id = target.id
                    JOIN data_access_envelopes AS source_envelope
                      ON source_envelope.resource_type = :source_type
                     AND source_envelope.resource_id = target.{id_column}
                    JOIN data_access_envelope_sources AS source
                      ON source.envelope_id = source_envelope.id
                    WHERE target.data_access_scope = 'governed'
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "policy_revision": policy_revision,
                    "source_type": source_type,
                    "target_type": target_type,
                },
            )


def _insert_unresolved_sources(bind, *, policy_revision: int) -> None:
    for resource_type, table_name, provable_shape in (
        (
            "ai_task_run",
            "ai_task_runs",
            """
            (resource.task_type = 'item_enrichment'
             AND resource.item_id IS NOT NULL
             AND resource.daily_brief_id IS NULL
             AND resource.report_id IS NULL)
            OR
            (resource.task_type = 'daily_brief'
             AND resource.item_id IS NULL
             AND resource.daily_brief_id IS NOT NULL
             AND resource.report_id IS NULL)
            OR
            (resource.task_type = 'report'
             AND resource.item_id IS NULL
             AND resource.daily_brief_id IS NULL
             AND resource.report_id IS NOT NULL)
            """,
        ),
        (
            "ai_usage_event",
            "ai_usage_events",
            """
            (resource.feature_type = 'item_enrichment'
             AND resource.item_id IS NOT NULL
             AND resource.daily_brief_id IS NULL
             AND resource.report_id IS NULL)
            OR
            (resource.feature_type = 'daily_brief'
             AND resource.item_id IS NULL
             AND resource.daily_brief_id IS NOT NULL
             AND resource.report_id IS NULL)
            OR
            (resource.feature_type = 'report'
             AND resource.item_id IS NULL
             AND resource.daily_brief_id IS NULL
             AND resource.report_id IS NOT NULL)
            """,
        ),
    ):
        bind.execute(
            sa.text(
                f"""
                INSERT INTO data_access_envelope_sources (
                    id, envelope_id, source_type, source_id, source_version,
                    source_feed_id, source_parent_id, handling_label_id,
                    captured_policy_revision, source_digest, captured_at
                )
                SELECT md5(
                           '0078:{resource_type}:unresolved:' || resource.id::text
                       )::uuid,
                       envelope.id, 'unresolved', resource.id::text,
                       'migrate:0078:unresolved', NULL, NULL, :quarantine,
                       :policy_revision, NULL, resource.created_at
                FROM {table_name} AS resource
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = :resource_type
                 AND envelope.resource_id = resource.id
                WHERE resource.data_access_scope = 'governed'
                  AND (
                      NOT ({provable_shape})
                      OR NOT EXISTS (
                          SELECT 1
                          FROM data_access_envelope_sources AS source
                          WHERE source.envelope_id = envelope.id
                      )
                  )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "policy_revision": policy_revision,
                "quarantine": _QUARANTINE_LABEL_ID,
                "resource_type": resource_type,
            },
        )


def _rebuild_ai_envelopes(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS envelope
            WHERE label.envelope_id = envelope.id
              AND envelope.resource_type IN ('ai_task_run', 'ai_usage_event')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_labels
                (envelope_id, label_id, source_count)
            SELECT source.envelope_id, source.handling_label_id,
                   count(*)::integer
            FROM data_access_envelope_sources AS source
            JOIN data_access_envelopes AS envelope
              ON envelope.id = source.envelope_id
            WHERE envelope.resource_type IN ('ai_task_run', 'ai_usage_event')
            GROUP BY source.envelope_id, source.handling_label_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            WITH totals AS (
                SELECT source.envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources AS source
                JOIN data_access_envelopes AS envelope
                  ON envelope.id = source.envelope_id
                WHERE envelope.resource_type IN (
                    'ai_task_run', 'ai_usage_event'
                )
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


def _backfill_audit_lineage(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO audit_log_data_access_labels (audit_log_id, label_id)
            SELECT DISTINCT audit.id, parsed.label_id
            FROM audit_logs AS audit
            CROSS JOIN LATERAL jsonb_array_elements_text(
                audit.data_access_label_ids
            ) AS labels(value)
            CROSS JOIN LATERAL (
                SELECT CASE
                    WHEN value ~ '{_UUID_PATTERN}' THEN value::uuid
                    ELSE NULL
                END AS label_id
            ) AS parsed
            JOIN handling_labels AS label ON label.id = parsed.label_id
            WHERE audit.data_access_governed
              AND parsed.label_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )
    _backfill_audit_envelope_links(bind)
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET data_access_governed = true
            WHERE NOT audit.data_access_governed
              AND (
                  EXISTS (
                      SELECT 1
                      FROM audit_log_data_access_labels AS label
                      WHERE label.audit_log_id = audit.id
                  )
                  OR (
                      audit.action LIKE 'ai.%'
                      AND audit.action NOT IN (
                          'ai.connection.test', 'ai.settings.update'
                      )
                  )
                  OR audit.action LIKE 'reports.generate.%'
              )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO audit_log_data_access_labels (audit_log_id, label_id)
            SELECT DISTINCT audit.id, CAST(:quarantine AS uuid)
            FROM audit_logs AS audit
            CROSS JOIN LATERAL jsonb_array_elements_text(
                audit.data_access_label_ids
            ) AS labels(value)
            WHERE audit.data_access_governed
              AND (
                  value !~ '{_UUID_PATTERN}'
                  OR NOT EXISTS (
                      SELECT 1
                      FROM handling_labels AS handling_label
                      WHERE handling_label.id = CASE
                          WHEN value ~ '{_UUID_PATTERN}' THEN value::uuid
                          ELSE NULL
                      END
                  )
              )
            ON CONFLICT DO NOTHING
            """
        ),
        {"quarantine": _QUARANTINE_LABEL_ID},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO audit_log_data_access_labels (audit_log_id, label_id)
            SELECT audit.id, :quarantine
            FROM audit_logs AS audit
            WHERE audit.data_access_governed
              AND NOT EXISTS (
                  SELECT 1 FROM audit_log_data_access_labels AS label
                  WHERE label.audit_log_id = audit.id
              )
            ON CONFLICT DO NOTHING
            """
        ),
        {"quarantine": _QUARANTINE_LABEL_ID},
    )


def _backfill_audit_envelope_links(bind) -> None:
    normalized_type = """
        CASE
            WHEN audit.resource_type = 'daily_brief' THEN 'ai_daily_brief'
            ELSE audit.resource_type
        END
    """
    bind.execute(
        sa.text(
            f"""
            INSERT INTO audit_log_data_access_feeds (
                audit_log_id, source_feed_id_snapshot
            )
            SELECT DISTINCT audit.id, source.source_feed_id
            FROM audit_logs AS audit
            JOIN data_access_envelopes AS envelope
              ON envelope.resource_type = {normalized_type}
             AND envelope.resource_id = CASE
                 WHEN COALESCE(audit.resource_id, '') ~ '{_UUID_PATTERN}'
                 THEN audit.resource_id::uuid
                 ELSE NULL
             END
            JOIN data_access_envelope_sources AS source
              ON source.envelope_id = envelope.id
            WHERE source.source_feed_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            WITH linked_runs AS (
                SELECT audit.id AS audit_id,
                       CASE
                           WHEN audit.resource_type = 'ai_task_run'
                            AND audit.resource_id ~ '{_UUID_PATTERN}'
                           THEN audit.resource_id::uuid
                           WHEN COALESCE(audit.metadata_json->>'run_id', '')
                                ~ '{_UUID_PATTERN}'
                           THEN (audit.metadata_json->>'run_id')::uuid
                           ELSE NULL
                       END AS run_id
                FROM audit_logs AS audit
                WHERE audit.action LIKE 'ai.%'
                   OR audit.action LIKE 'reports.generate.%'
            )
            INSERT INTO audit_log_data_access_feeds (
                audit_log_id, source_feed_id_snapshot
            )
            SELECT DISTINCT linked.audit_id, source.source_feed_id
            FROM linked_runs AS linked
            JOIN data_access_envelopes AS envelope
              ON envelope.resource_type = 'ai_task_run'
             AND envelope.resource_id = linked.run_id
            JOIN data_access_envelope_sources AS source
              ON source.envelope_id = envelope.id
            WHERE source.source_feed_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            WITH linked_envelopes AS (
                SELECT audit.id AS audit_id, envelope.id AS envelope_id
                FROM audit_logs AS audit
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = {normalized_type}
                 AND envelope.resource_id = CASE
                     WHEN COALESCE(audit.resource_id, '') ~ '{_UUID_PATTERN}'
                     THEN audit.resource_id::uuid
                     ELSE NULL
                 END
                UNION
                SELECT audit.id, envelope.id
                FROM audit_logs AS audit
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'ai_task_run'
                 AND envelope.resource_id = CASE
                     WHEN audit.resource_type = 'ai_task_run'
                      AND audit.resource_id ~ '{_UUID_PATTERN}'
                     THEN audit.resource_id::uuid
                     WHEN COALESCE(audit.metadata_json->>'run_id', '')
                          ~ '{_UUID_PATTERN}'
                     THEN (audit.metadata_json->>'run_id')::uuid
                     ELSE NULL
                 END
                WHERE audit.action LIKE 'ai.%'
                   OR audit.action LIKE 'reports.generate.%'
            )
            INSERT INTO audit_log_data_access_labels (audit_log_id, label_id)
            SELECT DISTINCT linked.audit_id, label.label_id
            FROM linked_envelopes AS linked
            JOIN data_access_envelope_labels AS label
              ON label.envelope_id = linked.envelope_id
            ON CONFLICT DO NOTHING
            """
        )
    )


def _install_audit_lineage_triggers(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_AUDIT_FENCE_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                PERFORM 1
                FROM data_policy_state
                WHERE id = 1
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'AI audit lineage capture requires data policy state.'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_AUDIT_FENCE_TRIGGER}
            BEFORE INSERT OR UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION {_AUDIT_FENCE_FUNCTION}()
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_AUDIT_CAPTURE_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                normalized_resource_type text;
                linked_run_id uuid;
                linked_governed_resource boolean;
                requires_ai_governance boolean;
            BEGIN
                normalized_resource_type := CASE
                    WHEN NEW.resource_type = 'daily_brief'
                        THEN 'ai_daily_brief'
                    ELSE NEW.resource_type
                END;
                linked_run_id := CASE
                    WHEN NEW.resource_type = 'ai_task_run'
                     AND COALESCE(NEW.resource_id, '') ~ '{_UUID_PATTERN}'
                    THEN NEW.resource_id::uuid
                    WHEN COALESCE(NEW.metadata_json->>'run_id', '')
                         ~ '{_UUID_PATTERN}'
                    THEN (NEW.metadata_json->>'run_id')::uuid
                    ELSE NULL
                END;
                linked_governed_resource := EXISTS (
                    SELECT 1
                    FROM data_access_envelopes AS envelope
                    WHERE (
                        envelope.resource_type = normalized_resource_type
                        AND envelope.resource_id = CASE
                            WHEN COALESCE(NEW.resource_id, '')
                                 ~ '{_UUID_PATTERN}'
                            THEN NEW.resource_id::uuid
                            ELSE NULL
                        END
                    ) OR (
                        linked_run_id IS NOT NULL
                        AND envelope.resource_type = 'ai_task_run'
                        AND envelope.resource_id = linked_run_id
                    )
                ) OR EXISTS (
                    SELECT 1
                    FROM audit_log_data_access_labels AS existing_label
                    WHERE existing_label.audit_log_id = NEW.id
                );
                requires_ai_governance := (
                    (
                        NEW.action LIKE 'ai.%'
                        AND NEW.action NOT IN (
                            'ai.connection.test', 'ai.settings.update'
                        )
                    )
                    OR NEW.action LIKE 'reports.generate.%'
                );
                IF NOT NEW.data_access_governed
                   AND (linked_governed_resource OR requires_ai_governance) THEN
                    UPDATE audit_logs
                    SET data_access_governed = true
                    WHERE id = NEW.id;
                    NEW.data_access_governed := true;
                END IF;
                IF NOT NEW.data_access_governed THEN
                    RETURN NEW;
                END IF;
                INSERT INTO audit_log_data_access_labels (audit_log_id, label_id)
                SELECT NEW.id, parsed.label_id
                FROM jsonb_array_elements_text(
                    NEW.data_access_label_ids
                ) AS labels(value)
                CROSS JOIN LATERAL (
                    SELECT CASE
                        WHEN value ~ '{_UUID_PATTERN}' THEN value::uuid
                        ELSE NULL
                    END AS label_id
                ) AS parsed
                JOIN handling_labels AS label ON label.id = parsed.label_id
                WHERE parsed.label_id IS NOT NULL
                ON CONFLICT DO NOTHING;

                IF COALESCE(NEW.resource_id, '') ~ '{_UUID_PATTERN}' THEN
                    INSERT INTO audit_log_data_access_feeds (
                        audit_log_id, source_feed_id_snapshot
                    )
                    SELECT NEW.id, source.source_feed_id
                    FROM data_access_envelopes AS envelope
                    JOIN data_access_envelope_sources AS source
                      ON source.envelope_id = envelope.id
                    WHERE envelope.resource_type = normalized_resource_type
                      AND envelope.resource_id = NEW.resource_id::uuid
                      AND source.source_feed_id IS NOT NULL
                    ON CONFLICT DO NOTHING;
                    INSERT INTO audit_log_data_access_labels (
                        audit_log_id, label_id
                    )
                    SELECT NEW.id, label.label_id
                    FROM data_access_envelopes AS envelope
                    JOIN data_access_envelope_labels AS label
                      ON label.envelope_id = envelope.id
                    WHERE envelope.resource_type = normalized_resource_type
                      AND envelope.resource_id = NEW.resource_id::uuid
                    ON CONFLICT DO NOTHING;
                END IF;

                IF linked_run_id IS NOT NULL THEN
                    INSERT INTO audit_log_data_access_feeds (
                        audit_log_id, source_feed_id_snapshot
                    )
                    SELECT NEW.id, source.source_feed_id
                    FROM data_access_envelopes AS envelope
                    JOIN data_access_envelope_sources AS source
                      ON source.envelope_id = envelope.id
                    WHERE envelope.resource_type = 'ai_task_run'
                      AND envelope.resource_id = linked_run_id
                      AND source.source_feed_id IS NOT NULL
                    ON CONFLICT DO NOTHING;
                    INSERT INTO audit_log_data_access_labels (
                        audit_log_id, label_id
                    )
                    SELECT NEW.id, label.label_id
                    FROM data_access_envelopes AS envelope
                    JOIN data_access_envelope_labels AS label
                      ON label.envelope_id = envelope.id
                    WHERE envelope.resource_type = 'ai_task_run'
                      AND envelope.resource_id = linked_run_id
                    ON CONFLICT DO NOTHING;
                END IF;

                IF NOT EXISTS (
                       SELECT 1
                       FROM audit_log_data_access_labels AS label
                       WHERE label.audit_log_id = NEW.id
                   )
                   OR EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements_text(
                           NEW.data_access_label_ids
                       ) AS labels(value)
                       WHERE value !~ '{_UUID_PATTERN}'
                          OR NOT EXISTS (
                              SELECT 1
                              FROM handling_labels AS handling_label
                              WHERE handling_label.id = CASE
                                  WHEN value ~ '{_UUID_PATTERN}'
                                  THEN value::uuid
                                  ELSE NULL
                              END
                          )
                   ) THEN
                    INSERT INTO audit_log_data_access_labels (
                        audit_log_id, label_id
                    ) VALUES (NEW.id, '{_QUARANTINE_LABEL_ID}'::uuid)
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_AUDIT_CAPTURE_TRIGGER}
            AFTER INSERT OR UPDATE OF
                action,
                resource_type,
                resource_id,
                metadata_json,
                data_access_governed,
                data_access_label_ids
            ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION {_AUDIT_CAPTURE_FUNCTION}()
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_AUDIT_FEED_TAINT_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO audit_log_data_access_labels (
                        audit_log_id, label_id
                    )
                    SELECT feed.audit_log_id, NEW.handling_label_id
                    FROM audit_log_data_access_feeds AS feed
                    WHERE feed.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_AUDIT_FEED_TAINT_TRIGGER}
            AFTER UPDATE OF handling_label_id ON feeds
            FOR EACH ROW
            EXECUTE FUNCTION {_AUDIT_FEED_TAINT_FUNCTION}()
            """
        )
    )


def _drop_audit_lineage_triggers(bind) -> None:
    bind.execute(sa.text(f"DROP TRIGGER {_AUDIT_FEED_TAINT_TRIGGER} ON feeds"))
    bind.execute(sa.text(f"DROP FUNCTION {_AUDIT_FEED_TAINT_FUNCTION}()"))
    bind.execute(sa.text(f"DROP TRIGGER {_AUDIT_CAPTURE_TRIGGER} ON audit_logs"))
    bind.execute(sa.text(f"DROP FUNCTION {_AUDIT_CAPTURE_FUNCTION}()"))
    bind.execute(sa.text(f"DROP TRIGGER {_AUDIT_FENCE_TRIGGER} ON audit_logs"))
    bind.execute(sa.text(f"DROP FUNCTION {_AUDIT_FENCE_FUNCTION}()"))


__all__ = ["downgrade", "upgrade"]
