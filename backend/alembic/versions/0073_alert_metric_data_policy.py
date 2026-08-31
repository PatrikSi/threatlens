"""retain policy cohorts for alert occurrence metric rollups

Revision ID: 0073_alert_metric_data_policy
Revises: 0072_report_ready_owner_envelope
Create Date: 2026-08-30
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0073_alert_metric_data_policy"
down_revision = "0072_report_ready_owner_envelope"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = "00000000-0000-4000-8000-000000000202"
_UNRESOLVED_FEED_ID = "00000000-0000-0000-0000-000000000000"
_LEGACY_POLICY_COHORT_KEY = hashlib.sha256(
    f"0:{_QUARANTINE_LABEL_ID}".encode("ascii")
).hexdigest()
_WRITER_GUARD_FUNCTION = "threatlens_guard_alert_metric_cohort_write_v1"
_WRITER_GUARD_TRIGGER = "trg_alert_metrics_guard_cohort_write_v1"
_FEED_TAINT_FUNCTION = "threatlens_taint_alert_metrics_for_feed_v1"
_FEED_TAINT_TRIGGER = "trg_feeds_taint_alert_metrics_v1"
_LABEL_ARCHIVE_FUNCTION = "threatlens_guard_alert_metric_label_archive_v1"
_LABEL_ARCHIVE_TRIGGER = "trg_handling_labels_guard_alert_metrics_v1"


def upgrade() -> None:
    bind = op.get_bind()
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
            "Cannot migrate alert metric policy cohorts because data policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            "Cannot migrate alert metric policy cohorts while data policy audit or "
            "enforcement is active. Disable data policy before the rolling upgrade."
        )
    bind.execute(
        sa.text(
            "LOCK TABLE feeds, alert_occurrence_metrics, handling_labels "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )

    op.create_table(
        "alert_occurrence_metric_cohorts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_feed_id_snapshot",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("policy_cohort_key", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "occurrence_count >= 0",
            name="ck_alert_occurrence_metric_cohorts_count",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"],
            ["alert_occurrence_metrics.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "source_feed_id_snapshot",
            "policy_cohort_key",
            name="uq_alert_occurrence_metric_cohorts_dimensions",
        ),
    )
    op.create_index(
        "ix_alert_occurrence_metric_cohorts_metric_id",
        "alert_occurrence_metric_cohorts",
        ["metric_id"],
    )
    op.create_index(
        "ix_alert_occurrence_metric_cohorts_source_feed",
        "alert_occurrence_metric_cohorts",
        ["source_feed_id_snapshot", "metric_id"],
    )
    op.create_table(
        "alert_occurrence_metric_cohort_labels",
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["alert_occurrence_metric_cohorts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["handling_labels.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("cohort_id", "label_id"),
    )
    op.create_index(
        "ix_alert_occurrence_metric_cohort_labels_label",
        "alert_occurrence_metric_cohort_labels",
        ["label_id", "cohort_id"],
    )
    op.execute(
        "INSERT INTO alert_occurrence_metric_cohorts "
        "(id, metric_id, source_feed_id_snapshot, policy_cohort_key, "
        "occurrence_count, created_at, updated_at) "
        "SELECT md5(metric.id::text || '|legacy')::uuid, metric.id, "
        f"'{_UNRESOLVED_FEED_ID}'::uuid, '{_LEGACY_POLICY_COHORT_KEY}', "
        "metric.occurrence_count, metric.created_at, metric.updated_at "
        "FROM alert_occurrence_metrics AS metric"
    )
    op.execute(
        "INSERT INTO alert_occurrence_metric_cohort_labels (cohort_id, label_id) "
        "SELECT cohort.id, "
        f"'{_QUARANTINE_LABEL_ID}'::uuid "
        "FROM alert_occurrence_metric_cohorts AS cohort"
    )

    op.execute(
        f"""
        CREATE FUNCTION {_WRITER_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_setting(
                'threatlens.alert_metric_cohort_write', true
            ) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION
                    'Alert metric writes require a policy-cohort-aware worker.'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_WRITER_GUARD_TRIGGER}
        BEFORE INSERT OR UPDATE OF occurrence_count
        ON alert_occurrence_metrics
        FOR EACH ROW
        EXECUTE FUNCTION {_WRITER_GUARD_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_FEED_TAINT_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                INSERT INTO alert_occurrence_metric_cohort_labels (
                    cohort_id, label_id
                )
                SELECT cohort.id, NEW.handling_label_id
                FROM alert_occurrence_metric_cohorts AS cohort
                WHERE cohort.source_feed_id_snapshot = NEW.id
                ON CONFLICT DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_FEED_TAINT_TRIGGER}
        AFTER UPDATE OF handling_label_id ON feeds
        FOR EACH ROW
        EXECUTE FUNCTION {_FEED_TAINT_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_LABEL_ARCHIVE_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.is_active AND NOT NEW.is_active AND EXISTS (
                SELECT 1
                FROM alert_occurrence_metric_cohort_labels AS cohort_label
                WHERE cohort_label.label_id = NEW.id
            ) THEN
                RAISE EXCEPTION
                    'Handling label is retained by historical alert metrics.'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_LABEL_ARCHIVE_TRIGGER}
        BEFORE UPDATE OF is_active ON handling_labels
        FOR EACH ROW
        EXECUTE FUNCTION {_LABEL_ARCHIVE_FUNCTION}()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
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
            "Cannot downgrade alert metric policy cohorts because data policy state is missing."
        )
    bind.execute(
        sa.text(
            "LOCK TABLE feeds, handling_labels, "
            "alert_occurrence_metric_cohort_labels, "
            "alert_occurrence_metric_cohorts, alert_occurrence_metrics "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            "Cannot downgrade alert metric policy cohorts while data policy audit or enforcement is active. Disable data policy first."
        )
    incompatible_count = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM alert_occurrence_metrics AS metric
                WHERE (
                    SELECT count(*)
                    FROM alert_occurrence_metric_cohorts AS cohort
                    WHERE cohort.metric_id = metric.id
                ) <> 1
                   OR NOT EXISTS (
                        SELECT 1
                        FROM alert_occurrence_metric_cohorts AS cohort
                        WHERE cohort.metric_id = metric.id
                          AND cohort.source_feed_id_snapshot = :unresolved_feed_id
                          AND cohort.policy_cohort_key = :legacy_cohort_key
                          AND cohort.occurrence_count = metric.occurrence_count
                          AND (
                              SELECT count(*)
                              FROM alert_occurrence_metric_cohort_labels AS label
                              WHERE label.cohort_id = cohort.id
                          ) = 1
                          AND EXISTS (
                              SELECT 1
                              FROM alert_occurrence_metric_cohort_labels AS label
                              WHERE label.cohort_id = cohort.id
                                AND label.label_id = :quarantine_label_id
                          )
                   )
                """
            ),
            {
                "unresolved_feed_id": _UNRESOLVED_FEED_ID,
                "legacy_cohort_key": _LEGACY_POLICY_COHORT_KEY,
                "quarantine_label_id": _QUARANTINE_LABEL_ID,
            },
        )
        or 0
    )
    if incompatible_count:
        raise RuntimeError(
            "Cannot downgrade alert metric policy cohorts while classified metric rollups exist. Retain this schema or remove those rollups under an approved maintenance procedure."
        )

    op.execute(f"DROP TRIGGER {_LABEL_ARCHIVE_TRIGGER} ON handling_labels")
    op.execute(f"DROP FUNCTION {_LABEL_ARCHIVE_FUNCTION}()")
    op.execute(f"DROP TRIGGER {_FEED_TAINT_TRIGGER} ON feeds")
    op.execute(f"DROP FUNCTION {_FEED_TAINT_FUNCTION}()")
    op.execute(f"DROP TRIGGER {_WRITER_GUARD_TRIGGER} ON alert_occurrence_metrics")
    op.execute(f"DROP FUNCTION {_WRITER_GUARD_FUNCTION}()")
    op.drop_index(
        "ix_alert_occurrence_metric_cohort_labels_label",
        table_name="alert_occurrence_metric_cohort_labels",
    )
    op.drop_table("alert_occurrence_metric_cohort_labels")
    op.drop_index(
        "ix_alert_occurrence_metric_cohorts_source_feed",
        table_name="alert_occurrence_metric_cohorts",
    )
    op.drop_index(
        "ix_alert_occurrence_metric_cohorts_metric_id",
        table_name="alert_occurrence_metric_cohorts",
    )
    op.drop_table("alert_occurrence_metric_cohorts")
