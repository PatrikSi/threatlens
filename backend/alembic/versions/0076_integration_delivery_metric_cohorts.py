"""retain policy cohorts for integration delivery metric rollups

Revision ID: 0076_integration_metric_cohorts
Revises: 0075_notification_lineage_repair
Create Date: 2026-08-31
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0076_integration_metric_cohorts"
down_revision = "0075_notification_lineage_repair"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = "00000000-0000-4000-8000-000000000202"
_WRITER_GUARD_FUNCTION = "threatlens_guard_integration_metric_cohort_write_v1"
_WRITER_GUARD_TRIGGER = "trg_integration_metrics_guard_cohort_write_v1"
_FEED_TAINT_FUNCTION = "threatlens_taint_integration_metrics_for_feed_v1"
_FEED_TAINT_TRIGGER = "trg_feeds_taint_integration_metrics_v1"
_LABEL_ARCHIVE_FUNCTION = "threatlens_guard_integration_metric_label_archive_v1"
_LABEL_ARCHIVE_TRIGGER = "trg_handling_labels_guard_integration_metrics_v1"


def upgrade() -> None:
    bind = op.get_bind()
    policy_revision = _require_disabled_data_policy(bind, operation="migrate")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                feeds,
                handling_labels,
                integration_deliveries,
                integration_delivery_metrics,
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
            "Cannot migrate integration delivery metric cohorts because the "
            "quarantine handling label is missing or inactive."
        )
    negative_metrics = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*) FROM integration_delivery_metrics
                WHERE succeeded_count < 0
                   OR failed_count < 0
                   OR dead_letter_count < 0
                   OR attempt_count < 0
                   OR duration_total_ms < 0
                   OR duration_max_ms < 0
                """
            )
        )
        or 0
    )
    if negative_metrics:
        raise RuntimeError(
            "Cannot migrate integration delivery metric cohorts while legacy "
            "aggregate rows contain negative counters."
        )

    op.create_table(
        "integration_delivery_metric_cohorts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_cohort_key", sa.String(length=64), nullable=False),
        sa.Column("captured_policy_revision", sa.Integer(), nullable=False),
        sa.Column("provenance_complete", sa.Boolean(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("dead_letter_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("duration_total_ms", sa.Integer(), nullable=False),
        sa.Column("duration_max_ms", sa.Integer(), nullable=False),
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
            "policy_cohort_key ~ '^[0-9a-f]{64}$'",
            name="ck_integration_delivery_metric_cohorts_key",
        ),
        sa.CheckConstraint(
            "captured_policy_revision >= 1",
            name="ck_integration_delivery_metric_cohorts_revision",
        ),
        sa.CheckConstraint(
            "source_count >= 0 AND (source_count > 0 OR NOT provenance_complete)",
            name="ck_integration_delivery_metric_cohorts_provenance",
        ),
        sa.CheckConstraint(
            "succeeded_count >= 0 AND failed_count >= 0 "
            "AND dead_letter_count >= 0 AND attempt_count >= 0 "
            "AND duration_total_ms >= 0 AND duration_max_ms >= 0",
            name="ck_integration_delivery_metric_cohorts_counters",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"],
            ["integration_delivery_metrics.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "policy_cohort_key",
            name="uq_integration_delivery_metric_cohorts_dimensions",
        ),
    )
    op.create_index(
        "ix_integration_delivery_metric_cohorts_metric",
        "integration_delivery_metric_cohorts",
        ["metric_id"],
    )
    op.create_table(
        "integration_delivery_metric_cohort_labels",
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["integration_delivery_metric_cohorts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["handling_labels.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("cohort_id", "label_id"),
    )
    op.create_index(
        "ix_integration_delivery_metric_cohort_labels_label",
        "integration_delivery_metric_cohort_labels",
        ["label_id", "cohort_id"],
    )
    op.create_table(
        "integration_delivery_metric_cohort_feeds",
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_feed_id_snapshot",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_feed_id_snapshot <> "
            "'00000000-0000-0000-0000-000000000000'::uuid",
            name="ck_integration_delivery_metric_cohort_feeds_nonzero",
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["integration_delivery_metric_cohorts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("cohort_id", "source_feed_id_snapshot"),
    )
    op.create_index(
        "ix_integration_delivery_metric_cohort_feeds_feed",
        "integration_delivery_metric_cohort_feeds",
        ["source_feed_id_snapshot", "cohort_id"],
    )

    legacy_key = _cohort_key(
        policy_revision=policy_revision,
        provenance_complete=False,
        source_count=0,
        label_ids=(_QUARANTINE_LABEL_ID,),
        feed_ids=(),
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO integration_delivery_metric_cohorts (
                id, metric_id, policy_cohort_key, captured_policy_revision,
                provenance_complete, source_count, succeeded_count,
                failed_count, dead_letter_count, attempt_count,
                duration_total_ms, duration_max_ms, created_at, updated_at
            )
            SELECT md5(metric.id::text || '|legacy-policy-cohort')::uuid,
                   metric.id, :cohort_key, :policy_revision, false, 0,
                   metric.succeeded_count, metric.failed_count,
                   metric.dead_letter_count, metric.attempt_count,
                   metric.duration_total_ms, metric.duration_max_ms,
                   metric.created_at, metric.updated_at
            FROM integration_delivery_metrics AS metric
            """
        ),
        {"cohort_key": legacy_key, "policy_revision": policy_revision},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO integration_delivery_metric_cohort_labels
                (cohort_id, label_id)
            SELECT cohort.id, :quarantine
            FROM integration_delivery_metric_cohorts AS cohort
            """
        ),
        {"quarantine": _QUARANTINE_LABEL_ID},
    )
    _install_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _require_disabled_data_policy(bind, operation="downgrade")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                feeds,
                handling_labels,
                integration_deliveries,
                integration_delivery_metrics,
                integration_delivery_metric_cohorts,
                integration_delivery_metric_cohort_labels,
                integration_delivery_metric_cohort_feeds
            IN ACCESS EXCLUSIVE MODE
            """
        )
    )
    incompatible = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM integration_delivery_metrics AS metric
                WHERE (
                    SELECT count(*)
                    FROM integration_delivery_metric_cohorts AS cohort
                    WHERE cohort.metric_id = metric.id
                ) <> 1
                   OR NOT EXISTS (
                        SELECT 1
                        FROM integration_delivery_metric_cohorts AS cohort
                        WHERE cohort.metric_id = metric.id
                          AND NOT cohort.provenance_complete
                          AND cohort.source_count = 0
                          AND cohort.succeeded_count = metric.succeeded_count
                          AND cohort.failed_count = metric.failed_count
                          AND cohort.dead_letter_count = metric.dead_letter_count
                          AND cohort.attempt_count = metric.attempt_count
                          AND cohort.duration_total_ms = metric.duration_total_ms
                          AND cohort.duration_max_ms = metric.duration_max_ms
                          AND NOT EXISTS (
                              SELECT 1
                              FROM integration_delivery_metric_cohort_feeds AS feed
                              WHERE feed.cohort_id = cohort.id
                          )
                          AND (
                              SELECT count(*)
                              FROM integration_delivery_metric_cohort_labels AS label
                              WHERE label.cohort_id = cohort.id
                          ) = 1
                          AND EXISTS (
                              SELECT 1
                              FROM integration_delivery_metric_cohort_labels AS label
                              WHERE label.cohort_id = cohort.id
                                AND label.label_id = :quarantine
                          )
                   )
                """
            ),
            {"quarantine": _QUARANTINE_LABEL_ID},
        )
        or 0
    )
    if incompatible:
        raise RuntimeError(
            "Cannot downgrade integration delivery metric cohorts while "
            "classified rollups exist. Retain this schema or remove those "
            "rollups under an approved maintenance procedure."
        )

    _drop_triggers(bind)
    op.drop_index(
        "ix_integration_delivery_metric_cohort_feeds_feed",
        table_name="integration_delivery_metric_cohort_feeds",
    )
    op.drop_table("integration_delivery_metric_cohort_feeds")
    op.drop_index(
        "ix_integration_delivery_metric_cohort_labels_label",
        table_name="integration_delivery_metric_cohort_labels",
    )
    op.drop_table("integration_delivery_metric_cohort_labels")
    op.drop_index(
        "ix_integration_delivery_metric_cohorts_metric",
        table_name="integration_delivery_metric_cohorts",
    )
    op.drop_table("integration_delivery_metric_cohorts")


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
            f"Cannot {operation} integration delivery metric cohorts because "
            "data policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} integration delivery metric cohorts while data "
            "policy audit or enforcement is active. Disable data policy first."
        )
    return int(state["revision"])


def _cohort_key(
    *,
    policy_revision: int,
    provenance_complete: bool,
    source_count: int,
    label_ids,
    feed_ids,
) -> str:
    labels = "|".join(sorted({str(value) for value in label_ids}))
    feeds = "|".join(sorted({str(value) for value in feed_ids}))
    canonical = (
        f"{max(1, int(policy_revision))}:"
        f"{int(bool(provenance_complete))}:{max(0, int(source_count))}:"
        f"{labels}:{feeds}"
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _install_triggers(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_WRITER_GUARD_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF current_setting(
                    'threatlens.integration_metric_cohort_write', true
                ) IS DISTINCT FROM 'on' THEN
                    RAISE EXCEPTION
                        'Integration metric writes require a policy-cohort-aware worker.'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_WRITER_GUARD_TRIGGER}
            BEFORE INSERT OR UPDATE
            ON integration_delivery_metrics
            FOR EACH ROW
            EXECUTE FUNCTION {_WRITER_GUARD_FUNCTION}()
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_FEED_TAINT_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO integration_delivery_metric_cohort_labels (
                        cohort_id, label_id
                    )
                    SELECT feed.cohort_id, NEW.handling_label_id
                    FROM integration_delivery_metric_cohort_feeds AS feed
                    WHERE feed.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_FEED_TAINT_TRIGGER}
            AFTER UPDATE OF handling_label_id ON feeds
            FOR EACH ROW
            EXECUTE FUNCTION {_FEED_TAINT_FUNCTION}()
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_LABEL_ARCHIVE_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.is_active AND NOT NEW.is_active AND EXISTS (
                    SELECT 1
                    FROM integration_delivery_metric_cohort_labels AS label
                    WHERE label.label_id = NEW.id
                ) THEN
                    RAISE EXCEPTION
                        'Handling label is retained by historical integration metrics.'
                        USING ERRCODE = '23503';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_LABEL_ARCHIVE_TRIGGER}
            BEFORE UPDATE OF is_active ON handling_labels
            FOR EACH ROW
            EXECUTE FUNCTION {_LABEL_ARCHIVE_FUNCTION}()
            """
        )
    )


def _drop_triggers(bind) -> None:
    bind.execute(
        sa.text(f"DROP TRIGGER {_LABEL_ARCHIVE_TRIGGER} ON handling_labels")
    )
    bind.execute(sa.text(f"DROP FUNCTION {_LABEL_ARCHIVE_FUNCTION}()"))
    bind.execute(sa.text(f"DROP TRIGGER {_FEED_TAINT_TRIGGER} ON feeds"))
    bind.execute(sa.text(f"DROP FUNCTION {_FEED_TAINT_FUNCTION}()"))
    bind.execute(
        sa.text(
            f"DROP TRIGGER {_WRITER_GUARD_TRIGGER} "
            "ON integration_delivery_metrics"
        )
    )
    bind.execute(sa.text(f"DROP FUNCTION {_WRITER_GUARD_FUNCTION}()"))
