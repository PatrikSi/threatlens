"""retain data-access snapshots on audit history

Revision ID: 0077_audit_policy_snapshots
Revises: 0076_integration_metric_cohorts
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0077_audit_policy_snapshots"
down_revision = "0076_integration_metric_cohorts"
branch_labels = None
depends_on = None


_ENVELOPE_RESOURCE_TYPES = (
    "alert_occurrence",
    "ai_daily_brief",
    "integration_delivery",
    "integration_event",
    "investigation",
    "report",
)
_DIRECT_RESOURCE_TYPES = (
    "feed",
    "item",
    "item_ai_enrichment",
    "daily_brief",
    "notification_webhook_delivery",
    "ai_task_run",
    "ai_provider_attempt_receipt",
)
_SNAPSHOT_FUNCTION = "threatlens_capture_audit_data_access_v1"
_SNAPSHOT_TRIGGER = "trg_audit_logs_capture_data_access_v1"


def upgrade() -> None:
    bind = op.get_bind()
    _require_disabled_data_policy(bind, operation="migrate")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                audit_logs,
                feeds,
                items,
                notification_webhook_deliveries,
                ai_task_runs,
                ai_provider_attempt_receipts,
                data_access_envelopes,
                data_access_envelope_labels
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "data_access_governed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "data_access_label_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    _backfill_metadata_snapshots(bind)
    _backfill_feed_snapshots(bind)
    _backfill_item_snapshots(bind)
    _backfill_envelope_snapshots(bind)
    _backfill_notification_delivery_snapshots(bind)
    _backfill_ai_task_snapshots(bind)
    _backfill_ai_receipt_snapshots(bind)
    _mark_unresolved_governed_rows(bind)

    op.create_check_constraint(
        "ck_audit_logs_data_access_label_ids",
        "audit_logs",
        "jsonb_typeof(data_access_label_ids) = 'array' AND "
        "NOT jsonb_path_exists(data_access_label_ids, "
        "'$[*] ? (@.type() != \"string\")')",
    )
    op.create_check_constraint(
        "ck_audit_logs_ungoverned_labels_empty",
        "audit_logs",
        "data_access_governed OR jsonb_array_length(data_access_label_ids) = 0",
    )
    op.create_index(
        "ix_audit_logs_data_access_label_ids",
        "audit_logs",
        ["data_access_label_ids"],
        unique=False,
        postgresql_using="gin",
    )
    _create_rolling_writer_guard(bind)


def downgrade() -> None:
    _require_disabled_data_policy(op.get_bind(), operation="downgrade")
    op.execute(f"DROP TRIGGER IF EXISTS {_SNAPSHOT_TRIGGER} ON audit_logs")
    op.execute(f"DROP FUNCTION IF EXISTS {_SNAPSHOT_FUNCTION}()")
    op.drop_index("ix_audit_logs_data_access_label_ids", table_name="audit_logs")
    op.drop_constraint(
        "ck_audit_logs_ungoverned_labels_empty",
        "audit_logs",
        type_="check",
    )
    op.drop_constraint(
        "ck_audit_logs_data_access_label_ids",
        "audit_logs",
        type_="check",
    )
    op.drop_column("audit_logs", "data_access_label_ids")
    op.drop_column("audit_logs", "data_access_governed")


def _require_disabled_data_policy(bind, *, operation: str) -> None:
    state = (
        bind.execute(
            sa.text(
                "SELECT mode, coverage_version FROM data_policy_state "
                "WHERE id = 1 FOR UPDATE"
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        raise RuntimeError(
            f"Cannot {operation} audit data-access snapshots because data "
            "policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} audit data-access snapshots while data policy "
            "audit or enforcement is active. Disable data policy first."
        )


def _backfill_metadata_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET data_access_governed = true,
                data_access_label_ids = COALESCE(
                    (
                        SELECT jsonb_agg(DISTINCT value ORDER BY value)
                        FROM jsonb_array_elements_text(
                            audit_logs.metadata_json->'handling_label_ids'
                        ) AS labels(value)
                    ),
                    '[]'::jsonb
                )
            WHERE jsonb_typeof(metadata_json->'handling_label_ids') = 'array'
            """
        )
    )


def _backfill_feed_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = jsonb_build_array(
                    feed.handling_label_id::text
                )
            FROM feeds AS feed
            WHERE audit.resource_type = 'feed'
              AND audit.resource_id = feed.id::text
              AND audit.data_access_governed = false
            """
        )
    )


def _backfill_item_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = jsonb_build_array(
                    feed.handling_label_id::text
                )
            FROM items AS item
            JOIN feeds AS feed ON feed.id = item.feed_id
            WHERE audit.resource_type IN ('item', 'item_ai_enrichment')
              AND audit.resource_id = item.id::text
              AND audit.data_access_governed = false
            """
        )
    )


def _backfill_envelope_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            WITH snapshots AS (
                SELECT
                    envelope.resource_type,
                    envelope.resource_id,
                    jsonb_agg(
                        DISTINCT label.label_id::text
                        ORDER BY label.label_id::text
                    ) AS label_ids
                FROM data_access_envelopes AS envelope
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                GROUP BY envelope.resource_type, envelope.resource_id
            )
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = snapshots.label_ids
            FROM snapshots
            WHERE (
                    audit.resource_type = snapshots.resource_type
                    OR (
                        audit.resource_type = 'daily_brief'
                        AND snapshots.resource_type = 'ai_daily_brief'
                    )
              )
              AND audit.resource_id = snapshots.resource_id::text
              AND audit.data_access_governed = false
            """
        )
    )


def _backfill_notification_delivery_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            WITH candidate_labels AS (
                SELECT audit.id AS audit_id, label.label_id
                FROM audit_logs AS audit
                JOIN notification_webhook_deliveries AS delivery
                  ON audit.resource_id = delivery.id::text
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'integration_delivery'
                 AND envelope.resource_id = delivery.integration_delivery_id
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                WHERE audit.resource_type = 'notification_webhook_delivery'
                UNION
                SELECT audit.id, feed.handling_label_id
                FROM audit_logs AS audit
                JOIN notification_webhook_deliveries AS delivery
                  ON audit.resource_id = delivery.id::text
                JOIN items AS item ON item.id = delivery.item_id
                JOIN feeds AS feed ON feed.id = item.feed_id
                WHERE audit.resource_type = 'notification_webhook_delivery'
                UNION
                SELECT audit.id, feed.handling_label_id
                FROM audit_logs AS audit
                JOIN notification_webhook_deliveries AS delivery
                  ON audit.resource_id = delivery.id::text
                JOIN feeds AS feed ON feed.id = delivery.feed_id
                WHERE audit.resource_type = 'notification_webhook_delivery'
            ), snapshots AS (
                SELECT
                    audit_id,
                    jsonb_agg(
                        DISTINCT label_id::text ORDER BY label_id::text
                    ) AS label_ids
                FROM candidate_labels
                GROUP BY audit_id
            )
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = snapshots.label_ids
            FROM snapshots
            WHERE audit.id = snapshots.audit_id
              AND audit.data_access_governed = false
            """
        )
    )


def _backfill_ai_task_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            WITH candidate_labels AS (
                SELECT audit.id AS audit_id, feed.handling_label_id AS label_id
                FROM audit_logs AS audit
                JOIN ai_task_runs AS run ON audit.resource_id = run.id::text
                JOIN items AS item ON item.id = run.item_id
                JOIN feeds AS feed ON feed.id = item.feed_id
                WHERE audit.resource_type = 'ai_task_run'
                UNION
                SELECT audit.id, label.label_id
                FROM audit_logs AS audit
                JOIN ai_task_runs AS run ON audit.resource_id = run.id::text
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'ai_daily_brief'
                 AND envelope.resource_id = run.daily_brief_id
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                WHERE audit.resource_type = 'ai_task_run'
                UNION
                SELECT audit.id, label.label_id
                FROM audit_logs AS audit
                JOIN ai_task_runs AS run ON audit.resource_id = run.id::text
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'report'
                 AND envelope.resource_id = run.report_id
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                WHERE audit.resource_type = 'ai_task_run'
            ), snapshots AS (
                SELECT
                    audit_id,
                    jsonb_agg(
                        DISTINCT label_id::text ORDER BY label_id::text
                    ) AS label_ids
                FROM candidate_labels
                GROUP BY audit_id
            )
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = snapshots.label_ids
            FROM snapshots
            WHERE audit.id = snapshots.audit_id
              AND audit.data_access_governed = false
            """
        )
    )


def _backfill_ai_receipt_snapshots(bind) -> None:
    bind.execute(
        sa.text(
            """
            WITH candidate_labels AS (
                SELECT audit.id AS audit_id, feed.handling_label_id AS label_id
                FROM audit_logs AS audit
                JOIN ai_provider_attempt_receipts AS receipt
                  ON audit.resource_id = receipt.id::text
                JOIN items AS item ON item.id = receipt.resource_id
                JOIN feeds AS feed ON feed.id = item.feed_id
                WHERE audit.resource_type = 'ai_provider_attempt_receipt'
                  AND receipt.resource_type IN ('item', 'item_ai_enrichment')
                UNION
                SELECT audit.id, label.label_id
                FROM audit_logs AS audit
                JOIN ai_provider_attempt_receipts AS receipt
                  ON audit.resource_id = receipt.id::text
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'ai_daily_brief'
                 AND envelope.resource_id = receipt.resource_id
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                WHERE audit.resource_type = 'ai_provider_attempt_receipt'
                  AND receipt.resource_type IN ('daily_brief', 'ai_daily_brief')
                UNION
                SELECT audit.id, label.label_id
                FROM audit_logs AS audit
                JOIN ai_provider_attempt_receipts AS receipt
                  ON audit.resource_id = receipt.id::text
                JOIN data_access_envelopes AS envelope
                  ON envelope.resource_type = 'report'
                 AND envelope.resource_id = receipt.resource_id
                JOIN data_access_envelope_labels AS label
                  ON label.envelope_id = envelope.id
                WHERE audit.resource_type = 'ai_provider_attempt_receipt'
                  AND receipt.resource_type = 'report'
            ), snapshots AS (
                SELECT
                    audit_id,
                    jsonb_agg(
                        DISTINCT label_id::text ORDER BY label_id::text
                    ) AS label_ids
                FROM candidate_labels
                GROUP BY audit_id
            )
            UPDATE audit_logs AS audit
            SET data_access_governed = true,
                data_access_label_ids = snapshots.label_ids
            FROM snapshots
            WHERE audit.id = snapshots.audit_id
              AND audit.data_access_governed = false
            """
        )
    )


def _mark_unresolved_governed_rows(bind) -> None:
    governed_types = tuple(
        sorted(set(_ENVELOPE_RESOURCE_TYPES) | set(_DIRECT_RESOURCE_TYPES))
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET data_access_governed = true,
                data_access_label_ids = '[]'::jsonb
            WHERE resource_id IS NOT NULL
              AND resource_type IN :resource_types
              AND data_access_governed = false
            """
        ).bindparams(
            sa.bindparam("resource_types", expanding=True),
        ),
        {"resource_types": governed_types},
    )


def _create_rolling_writer_guard(bind) -> None:
    envelope_types = ", ".join(
        f"'{resource_type}'" for resource_type in _ENVELOPE_RESOURCE_TYPES
    )
    governed_types = ", ".join(
        f"'{resource_type}'"
        for resource_type in sorted(
            set(_ENVELOPE_RESOURCE_TYPES) | set(_DIRECT_RESOURCE_TYPES)
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_SNAPSHOT_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                resolved_labels jsonb;
                normalized_resource_type text;
            BEGIN
                IF TG_OP = 'UPDATE'
                   AND OLD.data_access_governed
                   AND (
                       NEW.data_access_governed IS DISTINCT FROM
                           OLD.data_access_governed
                       OR NEW.data_access_label_ids IS DISTINCT FROM
                           OLD.data_access_label_ids
                   ) THEN
                    RAISE EXCEPTION
                        'audit data-access snapshots are immutable'
                        USING ERRCODE = 'check_violation';
                END IF;

                IF NEW.data_access_governed OR NEW.resource_id IS NULL THEN
                    RETURN NEW;
                END IF;

                IF jsonb_typeof(
                    NEW.metadata_json->'handling_label_ids'
                ) = 'array' THEN
                    NEW.data_access_governed := true;
                    NEW.data_access_label_ids :=
                        NEW.metadata_json->'handling_label_ids';
                    RETURN NEW;
                END IF;

                IF NEW.resource_id !~*
                    '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
                    '[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                    IF NEW.resource_type IN ({governed_types}) THEN
                        NEW.data_access_governed := true;
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.resource_type = 'feed' THEN
                    SELECT jsonb_build_array(feed.handling_label_id::text)
                    INTO resolved_labels
                    FROM feeds AS feed
                    WHERE feed.id = NEW.resource_id::uuid;
                ELSIF NEW.resource_type IN ('item', 'item_ai_enrichment') THEN
                    SELECT jsonb_build_array(feed.handling_label_id::text)
                    INTO resolved_labels
                    FROM items AS item
                    JOIN feeds AS feed ON feed.id = item.feed_id
                    WHERE item.id = NEW.resource_id::uuid;
                ELSIF NEW.resource_type IN ({envelope_types}, 'daily_brief') THEN
                    normalized_resource_type := CASE
                        WHEN NEW.resource_type = 'daily_brief'
                            THEN 'ai_daily_brief'
                        ELSE NEW.resource_type
                    END;
                    SELECT jsonb_agg(
                        DISTINCT label.label_id::text
                        ORDER BY label.label_id::text
                    )
                    INTO resolved_labels
                    FROM data_access_envelopes AS envelope
                    JOIN data_access_envelope_labels AS label
                      ON label.envelope_id = envelope.id
                    WHERE envelope.resource_type = normalized_resource_type
                      AND envelope.resource_id = NEW.resource_id::uuid;
                END IF;

                IF NEW.resource_type IN ({governed_types}) THEN
                    NEW.data_access_governed := true;
                    NEW.data_access_label_ids := COALESCE(
                        resolved_labels,
                        '[]'::jsonb
                    );
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
            CREATE TRIGGER {_SNAPSHOT_TRIGGER}
            BEFORE INSERT OR UPDATE OF
                resource_type,
                resource_id,
                metadata_json,
                data_access_governed,
                data_access_label_ids
            ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION {_SNAPSHOT_FUNCTION}()
            """
        )
    )


__all__ = ["downgrade", "upgrade"]
