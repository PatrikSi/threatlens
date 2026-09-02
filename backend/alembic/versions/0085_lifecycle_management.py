"""add configurable data lifecycle management

Revision ID: 0085_lifecycle_management
Revises: 0084_ioc_candidate_search
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0085_lifecycle_management"
down_revision = "0084_ioc_candidate_search"
branch_labels = None
depends_on = None


_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "lifecycle_catalog_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "catalog_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("bootstrapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "bootstrap_snapshot_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id = 1",
            name="ck_lifecycle_catalog_state_singleton",
        ),
        sa.CheckConstraint(
            "catalog_version = 1",
            name="ck_lifecycle_catalog_state_version",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "lifecycle_catalog_state",
            sa.column("id", sa.Integer()),
            sa.column("catalog_version", sa.Integer()),
            sa.column("bootstrap_snapshot_json", _JSON),
        ),
        [{"id": 1, "catalog_version": 1, "bootstrap_snapshot_json": {}}],
    )
    op.execute(
        """
        CREATE FUNCTION threatlens_enforce_lifecycle_catalog_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.catalog_version IS DISTINCT FROM OLD.catalog_version THEN
                RAISE EXCEPTION 'lifecycle catalog identity is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.bootstrapped_at IS NOT NULL AND (
                NEW.bootstrapped_at IS DISTINCT FROM OLD.bootstrapped_at
                OR NEW.bootstrap_snapshot_json
                    IS DISTINCT FROM OLD.bootstrap_snapshot_json
            ) THEN
                RAISE EXCEPTION 'lifecycle catalog bootstrap evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lifecycle_catalog_state_immutable
        BEFORE UPDATE ON lifecycle_catalog_state
        FOR EACH ROW
        EXECUTE FUNCTION threatlens_enforce_lifecycle_catalog_state()
        """
    )

    op.create_table(
        "lifecycle_policies",
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column(
            "schedule_cadence",
            sa.String(length=16),
            server_default="daily",
            nullable=False,
        ),
        sa.Column(
            "schedule_hour_utc",
            sa.Integer(),
            server_default="2",
            nullable=False,
        ),
        sa.Column("schedule_weekday", sa.Integer(), nullable=True),
        sa.Column(
            "max_records_per_run",
            sa.Integer(),
            server_default="10000",
            nullable=False,
        ),
        sa.Column(
            "options_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=16), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("updated_by_label_snapshot", sa.String(length=320), nullable=True),
        sa.Column(
            "configuration_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_key IN ("
            "'article_content', 'audit_logs', 'action_approval_history', "
            "'ai_task_history', 'ai_usage_history', 'tag_feedback_history', "
            "'integration_run_history', 'inactive_auth_sessions', "
            "'system_health_samples', 'integration_delivery_history', "
            "'integration_event_history', 'integration_metrics', "
            "'closed_alert_history', 'alert_activity_history', "
            "'alert_evaluation_history', 'alert_metrics'"
            ")",
            name="ck_lifecycle_policies_target_key",
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_lifecycle_policies_retention_days",
        ),
        sa.CheckConstraint(
            "schedule_cadence IN ('daily', 'weekly')",
            name="ck_lifecycle_policies_schedule_cadence",
        ),
        sa.CheckConstraint(
            "schedule_hour_utc BETWEEN 0 AND 23",
            name="ck_lifecycle_policies_schedule_hour_utc",
        ),
        sa.CheckConstraint(
            "schedule_weekday IS NULL OR schedule_weekday BETWEEN 0 AND 6",
            name="ck_lifecycle_policies_schedule_weekday",
        ),
        sa.CheckConstraint(
            "(schedule_cadence = 'weekly' AND schedule_weekday IS NOT NULL) "
            "OR (schedule_cadence = 'daily' AND schedule_weekday IS NULL)",
            name="ck_lifecycle_policies_schedule_shape",
        ),
        sa.CheckConstraint(
            "max_records_per_run BETWEEN 100 AND 100000",
            name="ck_lifecycle_policies_max_records_per_run",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_lifecycle_policies_revision",
        ),
        sa.CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_lifecycle_policies_last_run_status",
        ),
        sa.CheckConstraint(
            "(enabled AND next_run_at IS NOT NULL) OR "
            "(NOT enabled AND next_run_at IS NULL)",
            name="ck_lifecycle_policies_schedule_activation",
        ),
        sa.CheckConstraint(
            "updated_by_user_id IS NULL OR "
            "(updated_by_label_snapshot IS NOT NULL AND "
            "length(trim(updated_by_label_snapshot)) BETWEEN 1 AND 320)",
            name="ck_lifecycle_policies_actor_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_lifecycle_policies_updated_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("target_key"),
    )
    op.create_index(
        "ix_lifecycle_policies_due",
        "lifecycle_policies",
        ["enabled", "next_run_at"],
    )
    op.execute(
        """
        CREATE FUNCTION threatlens_enforce_lifecycle_policy_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            configuration_changed boolean;
        BEGIN
            IF NEW.target_key IS DISTINCT FROM OLD.target_key THEN
                RAISE EXCEPTION 'lifecycle policy target identity is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            configuration_changed :=
                NEW.enabled IS DISTINCT FROM OLD.enabled
                OR NEW.retention_days IS DISTINCT FROM OLD.retention_days
                OR NEW.schedule_cadence IS DISTINCT FROM OLD.schedule_cadence
                OR NEW.schedule_hour_utc IS DISTINCT FROM OLD.schedule_hour_utc
                OR NEW.schedule_weekday IS DISTINCT FROM OLD.schedule_weekday
                OR NEW.max_records_per_run
                    IS DISTINCT FROM OLD.max_records_per_run
                OR NEW.options_json IS DISTINCT FROM OLD.options_json;

            IF configuration_changed THEN
                IF NEW.revision <> OLD.revision + 1 THEN
                    RAISE EXCEPTION 'lifecycle policy changes must advance revision'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF coalesce(length(trim(NEW.updated_by_label_snapshot)), 0) = 0 THEN
                    RAISE EXCEPTION 'lifecycle policy changes require actor evidence'
                        USING ERRCODE = 'check_violation';
                END IF;
                NEW.configuration_updated_at := greatest(
                    clock_timestamp(),
                    OLD.configuration_updated_at + interval '1 microsecond'
                );
            ELSIF NEW.revision IS DISTINCT FROM OLD.revision
               OR NEW.configuration_updated_at
                    IS DISTINCT FROM OLD.configuration_updated_at
               OR NEW.updated_by_label_snapshot
                    IS DISTINCT FROM OLD.updated_by_label_snapshot
               OR (
                    NEW.updated_by_user_id IS DISTINCT FROM OLD.updated_by_user_id
                    AND NOT (
                        OLD.updated_by_user_id IS NOT NULL
                        AND NEW.updated_by_user_id IS NULL
                    )
               ) THEN
                RAISE EXCEPTION 'lifecycle policy attribution is configuration-only'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lifecycle_policies_evidence
        BEFORE UPDATE ON lifecycle_policies
        FOR EACH ROW
        EXECUTE FUNCTION threatlens_enforce_lifecycle_policy_evidence()
        """
    )

    op.create_table(
        "lifecycle_previews",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column(
            "policy_snapshot_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "eligible_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "protected_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "protected_counts_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("oldest_candidate_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligible_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "count_is_lower_bound",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "is_partial",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="ck_lifecycle_previews_policy_revision",
        ),
        sa.CheckConstraint(
            "eligible_count >= 0 AND protected_count >= 0 "
            "AND (eligible_bytes IS NULL OR eligible_bytes >= 0)",
            name="ck_lifecycle_previews_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "expires_at > generated_at",
            name="ck_lifecycle_previews_expiry",
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR "
            "(used_at >= generated_at AND used_at <= expires_at)",
            name="ck_lifecycle_previews_consumption_time",
        ),
        sa.ForeignKeyConstraint(
            ["target_key"],
            ["lifecycle_policies.target_key"],
            name="fk_lifecycle_previews_target_key",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_lifecycle_previews_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lifecycle_previews_target_generated",
        "lifecycle_previews",
        ["target_key", "generated_at"],
    )
    op.create_index(
        "ix_lifecycle_previews_expires_at",
        "lifecycle_previews",
        ["expires_at"],
    )
    op.execute(
        """
        CREATE FUNCTION threatlens_enforce_lifecycle_preview_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.target_key IS DISTINCT FROM OLD.target_key
               OR NEW.policy_revision IS DISTINCT FROM OLD.policy_revision
               OR NEW.policy_snapshot_json IS DISTINCT FROM OLD.policy_snapshot_json
               OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
               OR NEW.cutoff_at IS DISTINCT FROM OLD.cutoff_at
               OR NEW.eligible_count IS DISTINCT FROM OLD.eligible_count
               OR NEW.protected_count IS DISTINCT FROM OLD.protected_count
               OR NEW.protected_counts_json
                    IS DISTINCT FROM OLD.protected_counts_json
               OR NEW.oldest_candidate_at IS DISTINCT FROM OLD.oldest_candidate_at
               OR NEW.count_is_lower_bound
                    IS DISTINCT FROM OLD.count_is_lower_bound
               OR NEW.is_partial IS DISTINCT FROM OLD.is_partial
               OR NEW.eligible_bytes IS DISTINCT FROM OLD.eligible_bytes
               OR NEW.generated_at IS DISTINCT FROM OLD.generated_at
               OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
               OR (
                    NEW.requested_by_user_id
                        IS DISTINCT FROM OLD.requested_by_user_id
                    AND NOT (
                        OLD.requested_by_user_id IS NOT NULL
                        AND NEW.requested_by_user_id IS NULL
                    )
               ) THEN
                RAISE EXCEPTION 'lifecycle preview evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF OLD.used_at IS NOT NULL
               AND NEW.used_at IS DISTINCT FROM OLD.used_at THEN
                RAISE EXCEPTION 'lifecycle preview consumption is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lifecycle_previews_evidence_immutable
        BEFORE UPDATE ON lifecycle_previews
        FOR EACH ROW
        EXECUTE FUNCTION threatlens_enforce_lifecycle_preview_evidence()
        """
    )

    op.create_table(
        "lifecycle_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("trigger_source", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column(
            "policy_snapshot_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("preview_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("preview_id_snapshot", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "requested_by_user_id_snapshot",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column("requested_by_label_snapshot", sa.String(length=320), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_records", sa.Integer(), nullable=False),
        sa.Column(
            "evaluated_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "affected_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "protected_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "skipped_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "batch_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("remaining_count", sa.BigInteger(), nullable=True),
        sa.Column("affected_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "details_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested_by_user_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "cancel_requested_by_principal_type",
            sa.String(length=16),
            nullable=True,
        ),
        sa.Column(
            "cancel_requested_by_user_id_snapshot",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "cancel_requested_by_label_snapshot",
            sa.String(length=320),
            nullable=True,
        ),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger_source IN ('manual', 'scheduled')",
            name="ck_lifecycle_runs_trigger_source",
        ),
        sa.CheckConstraint(
            "status IN "
            "('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_lifecycle_runs_status",
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="ck_lifecycle_runs_policy_revision",
        ),
        sa.CheckConstraint(
            "max_records BETWEEN 100 AND 100000",
            name="ck_lifecycle_runs_max_records",
        ),
        sa.CheckConstraint(
            "evaluated_count >= 0 AND affected_count >= 0 "
            "AND protected_count >= 0 AND skipped_count >= 0 "
            "AND batch_count >= 0 "
            "AND (remaining_count IS NULL OR remaining_count >= 0) "
            "AND (affected_bytes IS NULL OR affected_bytes >= 0)",
            name="ck_lifecycle_runs_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "affected_count <= max_records",
            name="ck_lifecycle_runs_affected_within_limit",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND finished_at IS NULL) "
            "OR (status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL)",
            name="ck_lifecycle_runs_terminal_timestamp_shape",
        ),
        sa.CheckConstraint(
            "(trigger_source = 'scheduled' AND scheduled_for IS NOT NULL) "
            "OR (trigger_source = 'manual' AND scheduled_for IS NULL)",
            name="ck_lifecycle_runs_schedule_shape",
        ),
        sa.CheckConstraint(
            "(trigger_source = 'scheduled' AND preview_id IS NULL "
            "AND preview_id_snapshot IS NULL AND requested_by_user_id IS NULL "
            "AND requested_by_user_id_snapshot IS NULL "
            "AND requested_by_label_snapshot IS NULL "
            "AND idempotency_key_hash IS NULL AND request_fingerprint IS NULL) "
            "OR (trigger_source = 'manual' AND preview_id_snapshot IS NOT NULL "
            "AND requested_by_user_id_snapshot IS NOT NULL "
            "AND requested_by_label_snapshot IS NOT NULL "
            "AND length(trim(requested_by_label_snapshot)) BETWEEN 1 AND 320 "
            "AND reason IS NOT NULL AND length(trim(reason)) BETWEEN 10 AND 500 "
            "AND idempotency_key_hash IS NOT NULL "
            "AND length(idempotency_key_hash) = 64 "
            "AND request_fingerprint IS NOT NULL "
            "AND length(request_fingerprint) = 64)",
            name="ck_lifecycle_runs_actor_evidence",
        ),
        sa.CheckConstraint(
            "(NOT cancel_requested AND cancel_requested_at IS NULL "
            "AND cancel_requested_by_user_id IS NULL "
            "AND cancel_requested_by_principal_type IS NULL "
            "AND cancel_requested_by_user_id_snapshot IS NULL "
            "AND cancel_requested_by_label_snapshot IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(cancel_requested AND cancel_requested_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND length(trim(cancellation_reason)) BETWEEN 10 AND 500 "
            "AND ((cancel_requested_by_principal_type = 'user' "
            "AND cancel_requested_by_user_id_snapshot IS NOT NULL "
            "AND cancel_requested_by_label_snapshot IS NOT NULL "
            "AND length(trim(cancel_requested_by_label_snapshot)) BETWEEN 1 AND 320) "
            "OR (cancel_requested_by_principal_type = 'system' "
            "AND cancel_requested_by_user_id IS NULL "
            "AND cancel_requested_by_user_id_snapshot IS NULL "
            "AND cancel_requested_by_label_snapshot IS NULL)))",
            name="ck_lifecycle_runs_cancellation_shape",
        ),
        sa.CheckConstraint(
            "NOT cancel_requested OR status IN ('queued', 'running', 'cancelled')",
            name="ck_lifecycle_runs_cancellation_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_lifecycle_runs_lease_shape",
        ),
        sa.CheckConstraint(
            "cutoff_at <= queued_at",
            name="ck_lifecycle_runs_cutoff_before_queue",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND stop_reason IS NULL) OR "
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND stop_reason IS NOT NULL)",
            name="ck_lifecycle_runs_stop_reason_shape",
        ),
        sa.ForeignKeyConstraint(
            ["target_key"],
            ["lifecycle_policies.target_key"],
            name="fk_lifecycle_runs_target_key",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preview_id"],
            ["lifecycle_previews.id"],
            name="fk_lifecycle_runs_preview_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_lifecycle_runs_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cancel_requested_by_user_id"],
            ["users.id"],
            name="fk_lifecycle_runs_cancel_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requested_by_user_id",
            "target_key",
            "idempotency_key_hash",
            name="uq_lifecycle_runs_actor_target_idempotency",
        ),
        sa.UniqueConstraint(
            "target_key",
            "scheduled_for",
            name="uq_lifecycle_runs_target_scheduled_for",
        ),
    )
    op.create_index(
        "uq_lifecycle_runs_active_target",
        "lifecycle_runs",
        ["target_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_lifecycle_runs_target_created",
        "lifecycle_runs",
        ["target_key", "created_at"],
    )
    op.create_index(
        "ix_lifecycle_runs_status_created",
        "lifecycle_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_lifecycle_runs_trigger_created",
        "lifecycle_runs",
        ["trigger_source", "created_at"],
    )
    op.create_index(
        "ix_lifecycle_runs_created_at",
        "lifecycle_runs",
        ["created_at"],
    )
    op.create_index(
        "ix_lifecycle_runs_active_lease",
        "lifecycle_runs",
        ["status", "lease_expires_at"],
    )
    op.execute(
        """
        CREATE FUNCTION threatlens_enforce_lifecycle_run_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status IN ('succeeded', 'partial', 'failed', 'cancelled')
               AND (
                    NEW.status IS DISTINCT FROM OLD.status
                    OR NEW.started_at IS DISTINCT FROM OLD.started_at
                    OR NEW.finished_at IS DISTINCT FROM OLD.finished_at
                    OR NEW.stop_reason IS DISTINCT FROM OLD.stop_reason
                    OR NEW.error_code IS DISTINCT FROM OLD.error_code
                    OR NEW.error_message IS DISTINCT FROM OLD.error_message
                    OR NEW.evaluated_count IS DISTINCT FROM OLD.evaluated_count
                    OR NEW.affected_count IS DISTINCT FROM OLD.affected_count
                    OR NEW.protected_count IS DISTINCT FROM OLD.protected_count
                    OR NEW.skipped_count IS DISTINCT FROM OLD.skipped_count
                    OR NEW.batch_count IS DISTINCT FROM OLD.batch_count
                    OR NEW.remaining_count IS DISTINCT FROM OLD.remaining_count
                    OR NEW.affected_bytes IS DISTINCT FROM OLD.affected_bytes
                    OR NEW.details_json IS DISTINCT FROM OLD.details_json
                    OR NEW.celery_task_id IS DISTINCT FROM OLD.celery_task_id
                    OR NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
                    OR NEW.cancel_requested IS DISTINCT FROM OLD.cancel_requested
                    OR NEW.cancel_requested_at
                        IS DISTINCT FROM OLD.cancel_requested_at
                    OR NEW.cancel_requested_by_principal_type
                        IS DISTINCT FROM OLD.cancel_requested_by_principal_type
                    OR NEW.cancel_requested_by_user_id_snapshot
                        IS DISTINCT FROM OLD.cancel_requested_by_user_id_snapshot
                    OR NEW.cancel_requested_by_label_snapshot
                        IS DISTINCT FROM OLD.cancel_requested_by_label_snapshot
                    OR NEW.cancellation_reason
                        IS DISTINCT FROM OLD.cancellation_reason
                    OR (
                        NEW.cancel_requested_by_user_id
                            IS DISTINCT FROM OLD.cancel_requested_by_user_id
                        AND NOT (
                            OLD.cancel_requested_by_user_id IS NOT NULL
                            AND NEW.cancel_requested_by_user_id IS NULL
                        )
                    )
               ) THEN
                RAISE EXCEPTION 'terminal lifecycle run evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.target_key IS DISTINCT FROM OLD.target_key
               OR NEW.trigger_source IS DISTINCT FROM OLD.trigger_source
               OR NEW.policy_revision IS DISTINCT FROM OLD.policy_revision
               OR NEW.policy_snapshot_json IS DISTINCT FROM OLD.policy_snapshot_json
               OR NEW.preview_id_snapshot IS DISTINCT FROM OLD.preview_id_snapshot
               OR NEW.requested_by_user_id_snapshot
                    IS DISTINCT FROM OLD.requested_by_user_id_snapshot
               OR NEW.requested_by_label_snapshot
                    IS DISTINCT FROM OLD.requested_by_label_snapshot
               OR NEW.reason IS DISTINCT FROM OLD.reason
               OR (
                    NEW.preview_id IS DISTINCT FROM OLD.preview_id
                    AND NOT (
                        OLD.preview_id IS NOT NULL
                        AND NEW.preview_id IS NULL
                    )
               )
               OR (
                    NEW.requested_by_user_id
                        IS DISTINCT FROM OLD.requested_by_user_id
                    AND NOT (
                        OLD.requested_by_user_id IS NOT NULL
                        AND NEW.requested_by_user_id IS NULL
                    )
               )
               OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
               OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
               OR NEW.cutoff_at IS DISTINCT FROM OLD.cutoff_at
               OR NEW.scheduled_for IS DISTINCT FROM OLD.scheduled_for
               OR NEW.max_records IS DISTINCT FROM OLD.max_records
               OR NEW.queued_at IS DISTINCT FROM OLD.queued_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'lifecycle run request evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF OLD.cancel_requested AND (
                NOT NEW.cancel_requested
                OR NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at
                OR NEW.cancel_requested_by_principal_type
                    IS DISTINCT FROM OLD.cancel_requested_by_principal_type
                OR NEW.cancel_requested_by_user_id_snapshot
                    IS DISTINCT FROM OLD.cancel_requested_by_user_id_snapshot
                OR NEW.cancel_requested_by_label_snapshot
                    IS DISTINCT FROM OLD.cancel_requested_by_label_snapshot
                OR NEW.cancellation_reason IS DISTINCT FROM OLD.cancellation_reason
                OR (
                    NEW.cancel_requested_by_user_id
                        IS DISTINCT FROM OLD.cancel_requested_by_user_id
                    AND NOT (
                        OLD.cancel_requested_by_user_id IS NOT NULL
                        AND NEW.cancel_requested_by_user_id IS NULL
                    )
                )
            ) THEN
                RAISE EXCEPTION 'lifecycle cancellation evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.evaluated_count < OLD.evaluated_count
               OR NEW.affected_count < OLD.affected_count
               OR NEW.protected_count < OLD.protected_count
               OR NEW.skipped_count < OLD.skipped_count
               OR NEW.batch_count < OLD.batch_count
               OR coalesce(NEW.affected_bytes, 0) < coalesce(OLD.affected_bytes, 0) THEN
                RAISE EXCEPTION 'lifecycle run counters cannot decrease'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lifecycle_runs_evidence_immutable
        BEFORE UPDATE ON lifecycle_runs
        FOR EACH ROW
        EXECUTE FUNCTION threatlens_enforce_lifecycle_run_evidence()
        """
    )

    op.add_column(
        "articles",
        sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "articles",
        sa.Column("content_purge_run_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_articles_content_purge_run_id",
        "articles",
        "lifecycle_runs",
        ["content_purge_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_articles_content_purge_reference",
        "articles",
        "content_purge_run_id IS NULL OR content_purged_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_articles_content_purge_shape",
        "articles",
        "content_purged_at IS NULL OR "
        "(text IS NULL AND title_extracted IS NULL AND language IS NULL "
        "AND word_count IS NULL AND extraction_method = 'retention_purged')",
    )
    op.create_index(
        "ix_articles_retention_candidates",
        "articles",
        ["item_id", "id"],
        postgresql_where=sa.text(
            "content_purged_at IS NULL AND (text IS NOT NULL OR "
            "title_extracted IS NOT NULL OR language IS NOT NULL OR "
            "word_count IS NOT NULL)"
        ),
        sqlite_where=sa.text(
            "content_purged_at IS NULL AND (text IS NOT NULL OR "
            "title_extracted IS NOT NULL OR language IS NOT NULL OR "
            "word_count IS NOT NULL)"
        ),
    )
    op.create_index(
        "ix_articles_content_purge_run_id",
        "articles",
        ["content_purge_run_id"],
        postgresql_where=sa.text("content_purge_run_id IS NOT NULL"),
        sqlite_where=sa.text("content_purge_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_items_lifecycle_age",
        "items",
        [sa.text("COALESCE(published_at, first_seen_at)"), "id"],
    )
    op.create_index(
        "ix_integration_runs_lifecycle_finished",
        "integration_runs",
        ["finished_at", "id"],
        postgresql_where=sa.text("finished_at IS NOT NULL"),
        sqlite_where=sa.text("finished_at IS NOT NULL"),
    )
    op.create_index(
        "ix_integration_events_lifecycle_routed",
        "integration_events",
        ["created_at", "id"],
        postgresql_where=sa.text(
            "routing_state IN ('routed', 'dead_letter')"
        ),
        sqlite_where=sa.text("routing_state IN ('routed', 'dead_letter')"),
    )
    op.create_index(
        "ix_alert_evaluation_requests_lifecycle_terminal",
        "alert_evaluation_requests",
        ["completed_at", "id"],
        postgresql_where=sa.text(
            "state IN ('succeeded', 'dead_letter') "
            "AND completed_at IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "state IN ('succeeded', 'dead_letter') "
            "AND completed_at IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_integration_deliveries_lifecycle_terminal",
        "integration_deliveries",
        [
            sa.text(
                "COALESCE(completed_at, dead_lettered_at, updated_at)"
            ),
            "id",
        ],
        postgresql_where=sa.text(
            "state IN ('succeeded', 'failed', 'dead_letter') "
            "AND metrics_aggregated_at IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "state IN ('succeeded', 'failed', 'dead_letter') "
            "AND metrics_aggregated_at IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_auth_sessions_lifecycle_terminal",
        "auth_sessions",
        [
            sa.text(
                "COALESCE(revoked_at, LEAST(idle_expires_at, "
                "absolute_expires_at))"
            ),
            "id",
        ],
    )
    op.create_index(
        "ix_ai_provider_attempt_receipts_lifecycle_updated",
        "ai_provider_attempt_receipts",
        ["updated_at", "operation_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_provider_attempt_receipts_lifecycle_updated",
        table_name="ai_provider_attempt_receipts",
    )
    op.drop_index(
        "ix_auth_sessions_lifecycle_terminal",
        table_name="auth_sessions",
    )
    op.drop_index(
        "ix_integration_deliveries_lifecycle_terminal",
        table_name="integration_deliveries",
    )
    op.drop_index(
        "ix_integration_events_lifecycle_routed",
        table_name="integration_events",
    )
    op.drop_index(
        "ix_alert_evaluation_requests_lifecycle_terminal",
        table_name="alert_evaluation_requests",
    )
    op.drop_index(
        "ix_integration_runs_lifecycle_finished",
        table_name="integration_runs",
    )
    op.drop_index("ix_items_lifecycle_age", table_name="items")
    op.drop_index("ix_articles_content_purge_run_id", table_name="articles")
    op.drop_index("ix_articles_retention_candidates", table_name="articles")
    op.drop_constraint(
        "ck_articles_content_purge_shape",
        "articles",
        type_="check",
    )
    op.drop_constraint(
        "ck_articles_content_purge_reference",
        "articles",
        type_="check",
    )
    op.drop_constraint(
        "fk_articles_content_purge_run_id",
        "articles",
        type_="foreignkey",
    )
    op.drop_column("articles", "content_purge_run_id")
    op.drop_column("articles", "content_purged_at")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_lifecycle_runs_evidence_immutable "
        "ON lifecycle_runs"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS threatlens_enforce_lifecycle_run_evidence()"
    )
    op.drop_index("ix_lifecycle_runs_active_lease", table_name="lifecycle_runs")
    op.drop_index("ix_lifecycle_runs_created_at", table_name="lifecycle_runs")
    op.drop_index("ix_lifecycle_runs_trigger_created", table_name="lifecycle_runs")
    op.drop_index("ix_lifecycle_runs_status_created", table_name="lifecycle_runs")
    op.drop_index("ix_lifecycle_runs_target_created", table_name="lifecycle_runs")
    op.drop_index("uq_lifecycle_runs_active_target", table_name="lifecycle_runs")
    op.drop_table("lifecycle_runs")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_lifecycle_previews_evidence_immutable "
        "ON lifecycle_previews"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS threatlens_enforce_lifecycle_preview_evidence()"
    )
    op.drop_index("ix_lifecycle_previews_expires_at", table_name="lifecycle_previews")
    op.drop_index(
        "ix_lifecycle_previews_target_generated",
        table_name="lifecycle_previews",
    )
    op.drop_table("lifecycle_previews")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_lifecycle_policies_evidence "
        "ON lifecycle_policies"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS threatlens_enforce_lifecycle_policy_evidence()"
    )
    op.drop_index("ix_lifecycle_policies_due", table_name="lifecycle_policies")
    op.drop_table("lifecycle_policies")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lifecycle_catalog_state_immutable "
        "ON lifecycle_catalog_state"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS threatlens_enforce_lifecycle_catalog_state()"
    )
    op.drop_table("lifecycle_catalog_state")
