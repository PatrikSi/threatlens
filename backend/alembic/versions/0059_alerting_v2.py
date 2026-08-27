"""add durable alert evaluation and occurrence lifecycle

Revision ID: 0059_alerting_v2
Revises: 0058_investigations
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0059_alerting_v2"
down_revision = "0058_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_interests",
        sa.Column(
            "severity", sa.String(length=16), nullable=False, server_default="medium"
        ),
    )
    op.add_column(
        "alert_interests",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "alert_interests",
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "alert_interests",
        sa.Column("durable_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alert_interests",
        sa.Column("suppression_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alert_interests",
        sa.Column("suppression_reason", sa.String(length=500), nullable=True),
    )
    op.create_check_constraint(
        "ck_alert_interests_severity",
        "alert_interests",
        "severity IN ('low', 'medium', 'high', 'critical')",
    )
    op.create_check_constraint(
        "ck_alert_interests_revision", "alert_interests", "revision >= 1"
    )
    op.create_check_constraint(
        "ck_alert_interests_row_version", "alert_interests", "row_version >= 1"
    )
    op.create_check_constraint(
        "ck_alert_interests_suppression_pair",
        "alert_interests",
        "(suppression_until IS NULL AND suppression_reason IS NULL) OR "
        "(suppression_until IS NOT NULL AND suppression_reason IS NOT NULL)",
    )
    op.create_index(
        "ix_alert_interests_enabled_durable_since",
        "alert_interests",
        ["enabled", "durable_since"],
    )
    # This timestamp is the v2 cutover. Existing enabled rules begin evaluating
    # only intents persisted after this migration; no item scan occurs here.
    op.execute(
        sa.text(
            "UPDATE alert_interests SET durable_since = CURRENT_TIMESTAMP "
            "WHERE enabled IS TRUE AND durable_since IS NULL"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION threatlens_alert_interests_v2_compat()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                definition_changed boolean;
                enabled_changed boolean;
                suppression_changed boolean;
                cutover_changed boolean;
                mutation_changed boolean;
                reenabled boolean;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    NEW.revision := GREATEST(COALESCE(NEW.revision, 1), 1);
                    NEW.row_version := GREATEST(COALESCE(NEW.row_version, 1), 1);
                    IF NEW.enabled IS TRUE THEN
                        NEW.durable_since := COALESCE(
                            NEW.durable_since,
                            statement_timestamp()
                        );
                    ELSE
                        NEW.durable_since := NULL;
                    END IF;
                    RETURN NEW;
                END IF;

                definition_changed :=
                    NEW.user_id IS DISTINCT FROM OLD.user_id
                    OR NEW.name IS DISTINCT FROM OLD.name
                    OR NEW.category IS DISTINCT FROM OLD.category
                    OR NEW.keywords::jsonb IS DISTINCT FROM OLD.keywords::jsonb
                    OR NEW.severity IS DISTINCT FROM OLD.severity;
                enabled_changed := NEW.enabled IS DISTINCT FROM OLD.enabled;
                suppression_changed :=
                    NEW.suppression_until IS DISTINCT FROM OLD.suppression_until
                    OR NEW.suppression_reason IS DISTINCT FROM OLD.suppression_reason;
                cutover_changed :=
                    NEW.durable_since IS DISTINCT FROM OLD.durable_since;
                mutation_changed :=
                    definition_changed
                    OR enabled_changed
                    OR suppression_changed
                    OR cutover_changed;
                reenabled := NEW.enabled IS TRUE AND OLD.enabled IS FALSE;

                IF definition_changed OR reenabled THEN
                    IF NEW.revision IS NOT DISTINCT FROM OLD.revision THEN
                        -- Legacy writers do not know about the revision column.
                        NEW.revision := OLD.revision + 1;
                    ELSIF NEW.revision <> OLD.revision + 1 THEN
                        RAISE EXCEPTION
                            'alert rule revision must advance exactly once (old %, new %)',
                            OLD.revision,
                            NEW.revision
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ck_alert_interests_revision_transition';
                    END IF;

                    IF NEW.enabled IS TRUE AND (
                        NEW.durable_since IS NULL
                        OR NEW.durable_since IS NOT DISTINCT FROM OLD.durable_since
                    ) THEN
                        -- A legacy definition update starts a new notification cutover.
                        NEW.durable_since := statement_timestamp();
                    END IF;
                ELSIF NEW.revision IS DISTINCT FROM OLD.revision THEN
                    RAISE EXCEPTION
                        'alert rule revision cannot change without a definition update or re-enable (old %, new %)',
                        OLD.revision,
                        NEW.revision
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_alert_interests_revision_transition';
                END IF;

                IF mutation_changed THEN
                    IF NEW.row_version IS NOT DISTINCT FROM OLD.row_version THEN
                        -- Legacy writers do not know about the row-version column.
                        NEW.row_version := OLD.row_version + 1;
                    ELSIF NEW.row_version <> OLD.row_version + 1 THEN
                        RAISE EXCEPTION
                            'alert rule row version must advance exactly once (old %, new %)',
                            OLD.row_version,
                            NEW.row_version
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'ck_alert_interests_row_version_transition';
                    END IF;
                ELSIF NEW.row_version IS DISTINCT FROM OLD.row_version THEN
                    RAISE EXCEPTION
                        'alert rule row version cannot change without a rule mutation (old %, new %)',
                        OLD.row_version,
                        NEW.row_version
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_alert_interests_row_version_transition';
                END IF;

                IF NEW.enabled IS TRUE AND NEW.durable_since IS NULL THEN
                    NEW.durable_since := statement_timestamp();
                ELSIF NEW.enabled IS FALSE THEN
                    NEW.durable_since := NULL;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_alert_interests_v2_compat
            BEFORE INSERT OR UPDATE ON alert_interests
            FOR EACH ROW
            EXECUTE FUNCTION threatlens_alert_interests_v2_compat()
            """
        )
    )

    op.create_table(
        "alert_evaluation_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("item_content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="live"
        ),
        sa.Column(
            "active_source",
            sa.String(length=16),
            nullable=False,
            server_default="live",
        ),
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "notify_existing_occurrences",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "respect_rule_cutover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "dispatch_attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "dispatch_failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "accepted_rule_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "accepted_match_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "degraded_owner_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "degraded_owners_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("backfill_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatch_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_backfill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "evaluated_rule_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
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
            "state IN ('pending', 'processing', 'retry_wait', 'succeeded', 'dead_letter')",
            name="ck_alert_evaluation_requests_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_alert_evaluation_requests_attempt_count"
        ),
        sa.CheckConstraint(
            "max_attempts >= 1", name="ck_alert_evaluation_requests_max_attempts"
        ),
        sa.CheckConstraint(
            "source IN ('live', 'reconciliation', 'backfill')",
            name="ck_alert_evaluation_requests_source",
        ),
        sa.CheckConstraint(
            "active_source IN ('live', 'reconciliation', 'backfill', 'replay')",
            name="ck_alert_evaluation_requests_active_source",
        ),
        sa.CheckConstraint(
            "dispatch_attempt_count >= 0",
            name="ck_alert_evaluation_requests_dispatch_attempt_count",
        ),
        sa.CheckConstraint(
            "dispatch_failure_count >= 0",
            name="ck_alert_evaluation_requests_dispatch_failure_count",
        ),
        sa.CheckConstraint("version >= 1", name="ck_alert_evaluation_requests_version"),
        sa.CheckConstraint(
            "accepted_rule_count >= 0 AND accepted_match_count >= 0",
            name="ck_alert_evaluation_requests_accepted_counts",
        ),
        sa.CheckConstraint(
            "degraded_owner_count >= 0",
            name="ck_alert_evaluation_requests_degraded_owner_count",
        ),
        sa.CheckConstraint(
            "backfill_count >= 0",
            name="ck_alert_evaluation_requests_backfill_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "item_content_hash",
            name="uq_alert_evaluation_requests_item_content",
        ),
    )
    op.create_index(
        "ix_alert_evaluation_requests_item_id", "alert_evaluation_requests", ["item_id"]
    )
    op.create_index(
        "ix_alert_evaluation_requests_recovery",
        "alert_evaluation_requests",
        ["state", "available_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_alert_evaluation_requests_dispatch_claim",
        "alert_evaluation_requests",
        ["dispatch_claimed_at"],
    )
    op.create_index(
        "ix_alert_evaluation_requests_dispatch_failure",
        "alert_evaluation_requests",
        ["state", "last_dispatch_failed_at"],
    )
    op.create_index(
        "ix_alert_evaluation_requests_retention",
        "alert_evaluation_requests",
        ["state", "completed_at"],
    )

    op.create_table(
        "alert_backfill_previews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_limit", sa.Integer(), nullable=False),
        sa.Column("cursor_first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_item_id", sa.Uuid(), nullable=True),
        sa.Column(
            "candidates_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_more", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "next_cursor_first_seen_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("next_cursor_item_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "item_limit >= 1 AND item_limit <= 500",
            name="ck_alert_backfill_previews_item_limit",
        ),
        sa.CheckConstraint(
            "matched_count >= 0",
            name="ck_alert_backfill_previews_matched_count",
        ),
        sa.CheckConstraint(
            "(cursor_first_seen_at IS NULL AND cursor_item_id IS NULL) OR "
            "(cursor_first_seen_at IS NOT NULL AND cursor_item_id IS NOT NULL)",
            name="ck_alert_backfill_previews_cursor_pair",
        ),
        sa.CheckConstraint(
            "(next_cursor_first_seen_at IS NULL AND next_cursor_item_id IS NULL) OR "
            "(next_cursor_first_seen_at IS NOT NULL AND next_cursor_item_id IS NOT NULL)",
            name="ck_alert_backfill_previews_next_cursor_pair",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_backfill_previews_actor_expiry",
        "alert_backfill_previews",
        ["actor_user_id", "expires_at"],
    )
    op.create_index(
        "ix_alert_backfill_previews_expiry",
        "alert_backfill_previews",
        ["expires_at"],
    )

    op.create_table(
        "alert_evaluation_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("alert_interest_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("alert_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("alert_category_snapshot", sa.String(length=64), nullable=False),
        sa.Column(
            "alert_keywords_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "matched_keywords",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("severity_snapshot", sa.String(length=16), nullable=False),
        sa.Column(
            "suppressed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("suppression_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rule_revision >= 1", name="ck_alert_evaluation_matches_rule_revision"
        ),
        sa.CheckConstraint(
            "severity_snapshot IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_evaluation_matches_severity",
        ),
        sa.CheckConstraint(
            "(suppressed IS FALSE AND suppression_reason IS NULL) OR "
            "(suppressed IS TRUE AND suppression_reason IS NOT NULL)",
            name="ck_alert_evaluation_matches_suppression",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["alert_evaluation_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            "alert_interest_id",
            "rule_revision",
            name="uq_alert_evaluation_matches_request_rule_revision",
        ),
    )
    op.create_index(
        "ix_alert_evaluation_matches_request_owner",
        "alert_evaluation_matches",
        ["request_id", "owner_user_id", "id"],
    )
    op.create_index(
        "ix_alert_evaluation_matches_owner_user_id",
        "alert_evaluation_matches",
        ["owner_user_id"],
    )

    op.create_table(
        "alert_evaluation_request_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "details_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["alert_evaluation_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_evaluation_request_activities_request_created",
        "alert_evaluation_request_activities",
        ["request_id", "created_at", "id"],
    )
    op.create_index(
        "ix_alert_evaluation_request_activities_actor_user_id",
        "alert_evaluation_request_activities",
        ["actor_user_id"],
    )

    op.create_table(
        "alert_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_interest_id", sa.Uuid(), nullable=True),
        sa.Column("rule_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("item_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("integration_event_id", sa.Uuid(), nullable=True),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("item_content_hash", sa.String(length=64), nullable=False),
        sa.Column("alert_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("alert_category_snapshot", sa.String(length=64), nullable=False),
        sa.Column(
            "alert_keywords_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "matched_keywords",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "source_snapshot_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("severity_snapshot", sa.String(length=16), nullable=False),
        sa.Column(
            "lifecycle_state",
            sa.String(length=16),
            nullable=False,
            server_default="new",
        ),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppression_reason", sa.String(length=500), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snooze_reason", sa.String(length=500), nullable=True),
        sa.Column("closure_disposition", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("investigating_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("investigating_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("metrics_aggregated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            "lifecycle_state IN ('new', 'acknowledged', 'investigating', 'closed')",
            name="ck_alert_occurrences_lifecycle_state",
        ),
        sa.CheckConstraint(
            "severity_snapshot IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_occurrences_severity",
        ),
        sa.CheckConstraint(
            "rule_revision >= 1", name="ck_alert_occurrences_rule_revision"
        ),
        sa.CheckConstraint("version >= 1", name="ck_alert_occurrences_version"),
        sa.CheckConstraint(
            "(lifecycle_state = 'closed' AND closure_disposition IS NOT NULL) OR "
            "(lifecycle_state <> 'closed' AND closure_disposition IS NULL)",
            name="ck_alert_occurrences_closed_disposition",
        ),
        sa.CheckConstraint(
            "(suppressed_at IS NULL AND suppression_reason IS NULL) OR "
            "(suppressed_at IS NOT NULL AND suppression_reason IS NOT NULL)",
            name="ck_alert_occurrences_suppression_pair",
        ),
        sa.CheckConstraint(
            "(snoozed_until IS NULL AND snooze_reason IS NULL) OR "
            "(snoozed_until IS NOT NULL AND snooze_reason IS NOT NULL)",
            name="ck_alert_occurrences_snooze_pair",
        ),
        sa.ForeignKeyConstraint(
            ["alert_interest_id"], ["alert_interests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["integration_event_id"], ["integration_events.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["investigating_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id_snapshot",
            "rule_revision",
            "item_id_snapshot",
            "item_content_hash",
            name="uq_alert_occurrences_rule_revision_item_content",
        ),
    )
    for name, columns in (
        ("ix_alert_occurrences_owner_user_id", ["owner_user_id"]),
        ("ix_alert_occurrences_integration_event_id", ["integration_event_id"]),
        ("ix_alert_occurrences_item_id", ["item_id"]),
        ("ix_alert_occurrences_rule_id", ["alert_interest_id"]),
        ("ix_alert_occurrences_acknowledged_by_user_id", ["acknowledged_by_user_id"]),
        ("ix_alert_occurrences_investigating_by_user_id", ["investigating_by_user_id"]),
        ("ix_alert_occurrences_closed_by_user_id", ["closed_by_user_id"]),
        (
            "ix_alert_occurrences_retention",
            ["lifecycle_state", "closed_at", "metrics_aggregated_at"],
        ),
        (
            "ix_alert_occurrences_owner_state_created",
            ["owner_user_id", "lifecycle_state", "created_at"],
        ),
        (
            "ix_alert_occurrences_owner_severity_created",
            ["owner_user_id", "severity_snapshot", "created_at"],
        ),
    ):
        op.create_index(name, "alert_occurrences", columns)

    op.create_table(
        "alert_occurrence_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "details_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["alert_occurrences.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_occurrence_activities_occurrence_id",
        "alert_occurrence_activities",
        ["occurrence_id"],
    )
    op.create_index(
        "ix_alert_occurrence_activities_occurrence_created",
        "alert_occurrence_activities",
        ["occurrence_id", "created_at"],
    )
    op.create_index(
        "ix_alert_occurrence_activities_actor_user_id",
        "alert_occurrence_activities",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_alert_occurrence_activities_created_at",
        "alert_occurrence_activities",
        ["created_at"],
    )

    op.create_table(
        "alert_occurrence_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column(
            "suppressed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
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
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_occurrence_metrics_severity",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('new', 'acknowledged', 'investigating', 'closed')",
            name="ck_alert_occurrence_metrics_lifecycle_state",
        ),
        sa.CheckConstraint(
            "occurrence_count >= 0", name="ck_alert_occurrence_metrics_count"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bucket_start",
            "owner_user_id",
            "severity",
            "lifecycle_state",
            "suppressed",
            name="uq_alert_occurrence_metrics_bucket_dimensions",
        ),
    )
    op.create_index(
        "ix_alert_occurrence_metrics_owner_bucket",
        "alert_occurrence_metrics",
        ["owner_user_id", "bucket_start"],
    )
    op.create_index(
        "ix_alert_occurrence_metrics_bucket_start",
        "alert_occurrence_metrics",
        ["bucket_start"],
    )

    # Alerting v1 emitted alert_match integration events directly from the
    # classification worker. Fence those writes at the database so an older
    # worker cannot run beside the durable evaluation pipeline and duplicate
    # notifications after this migration is active.
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION threatlens_alerting_v2_event_fence()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.event_type = 'alert_match'
                   AND NULLIF(
                       COALESCE(NEW.payload_json::jsonb, '{}'::jsonb)
                           ->> 'evaluation_request_id',
                       ''
                   ) IS NULL THEN
                    RAISE EXCEPTION
                        'Alerting v2 rejected a legacy alert_match event without an evaluation_request_id'
                        USING ERRCODE = '55000',
                              HINT =
                                  'Stop and upgrade every classification and alert worker before resuming processing.';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_alerting_v2_event_fence
            BEFORE INSERT OR UPDATE OF event_type, payload_json
            ON integration_events
            FOR EACH ROW
            EXECUTE FUNCTION threatlens_alerting_v2_event_fence()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_alerting_v2_event_fence ON integration_events"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS threatlens_alerting_v2_event_fence()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_alert_interests_v2_compat ON alert_interests"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS threatlens_alert_interests_v2_compat()")
    )
    op.drop_index(
        "ix_alert_backfill_previews_expiry",
        table_name="alert_backfill_previews",
    )
    op.drop_index(
        "ix_alert_backfill_previews_actor_expiry",
        table_name="alert_backfill_previews",
    )
    op.drop_table("alert_backfill_previews")
    op.drop_index(
        "ix_alert_occurrence_metrics_bucket_start",
        table_name="alert_occurrence_metrics",
    )
    op.drop_index(
        "ix_alert_occurrence_metrics_owner_bucket",
        table_name="alert_occurrence_metrics",
    )
    op.drop_table("alert_occurrence_metrics")
    op.drop_index(
        "ix_alert_occurrence_activities_created_at",
        table_name="alert_occurrence_activities",
    )
    op.drop_index(
        "ix_alert_occurrence_activities_actor_user_id",
        table_name="alert_occurrence_activities",
    )
    op.drop_index(
        "ix_alert_occurrence_activities_occurrence_created",
        table_name="alert_occurrence_activities",
    )
    op.drop_index(
        "ix_alert_occurrence_activities_occurrence_id",
        table_name="alert_occurrence_activities",
    )
    op.drop_table("alert_occurrence_activities")
    for name in (
        "ix_alert_occurrences_owner_severity_created",
        "ix_alert_occurrences_owner_state_created",
        "ix_alert_occurrences_retention",
        "ix_alert_occurrences_rule_id",
        "ix_alert_occurrences_closed_by_user_id",
        "ix_alert_occurrences_investigating_by_user_id",
        "ix_alert_occurrences_acknowledged_by_user_id",
        "ix_alert_occurrences_item_id",
        "ix_alert_occurrences_integration_event_id",
        "ix_alert_occurrences_owner_user_id",
    ):
        op.drop_index(name, table_name="alert_occurrences")
    op.drop_table("alert_occurrences")
    op.drop_index(
        "ix_alert_evaluation_request_activities_actor_user_id",
        table_name="alert_evaluation_request_activities",
    )
    op.drop_index(
        "ix_alert_evaluation_request_activities_request_created",
        table_name="alert_evaluation_request_activities",
    )
    op.drop_table("alert_evaluation_request_activities")
    op.drop_index(
        "ix_alert_evaluation_matches_owner_user_id",
        table_name="alert_evaluation_matches",
    )
    op.drop_index(
        "ix_alert_evaluation_matches_request_owner",
        table_name="alert_evaluation_matches",
    )
    op.drop_table("alert_evaluation_matches")
    op.drop_index(
        "ix_alert_evaluation_requests_retention",
        table_name="alert_evaluation_requests",
    )
    op.drop_index(
        "ix_alert_evaluation_requests_dispatch_failure",
        table_name="alert_evaluation_requests",
    )
    op.drop_index(
        "ix_alert_evaluation_requests_dispatch_claim",
        table_name="alert_evaluation_requests",
    )
    op.drop_index(
        "ix_alert_evaluation_requests_recovery", table_name="alert_evaluation_requests"
    )
    op.drop_index(
        "ix_alert_evaluation_requests_item_id", table_name="alert_evaluation_requests"
    )
    op.drop_table("alert_evaluation_requests")
    op.drop_index(
        "ix_alert_interests_enabled_durable_since", table_name="alert_interests"
    )
    op.drop_constraint(
        "ck_alert_interests_suppression_pair",
        "alert_interests",
        type_="check",
    )
    op.drop_constraint("ck_alert_interests_revision", "alert_interests", type_="check")
    op.drop_constraint(
        "ck_alert_interests_row_version", "alert_interests", type_="check"
    )
    op.drop_constraint("ck_alert_interests_severity", "alert_interests", type_="check")
    op.drop_column("alert_interests", "suppression_reason")
    op.drop_column("alert_interests", "suppression_until")
    op.drop_column("alert_interests", "durable_since")
    op.drop_column("alert_interests", "row_version")
    op.drop_column("alert_interests", "revision")
    op.drop_column("alert_interests", "severity")
