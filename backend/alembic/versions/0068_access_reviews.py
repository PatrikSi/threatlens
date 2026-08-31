"""add immutable access-review campaign history

Revision ID: 0068_access_reviews
Revises: 0067_action_approvals
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0068_access_reviews"
down_revision = "0067_action_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_review_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "scope_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("scope_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="open"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("closed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("closed_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("apply_started_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "apply_started_by_email_snapshot", sa.String(length=320), nullable=True
        ),
        sa.Column("apply_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_run_id", sa.Uuid(), nullable=True),
        sa.Column("applied_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("applied_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("cancelled_by_principal_type", sa.String(length=16), nullable=True),
        sa.Column("cancelled_by_email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("quarantined_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("quarantined_by_principal_type", sa.String(length=16), nullable=True),
        sa.Column(
            "quarantined_by_email_snapshot", sa.String(length=320), nullable=True
        ),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
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
            "status IN ('open', 'closed', 'applying', 'applied', "
            "'cancelled', 'quarantined')",
            name="ck_access_review_campaigns_status",
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND length(name) BETWEEN 3 AND 160 "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_access_review_campaigns_name",
        ),
        sa.CheckConstraint(
            "description = btrim(description) AND length(description) <= 2000",
            name="ck_access_review_campaigns_description",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' "
            "AND octet_length(scope_snapshot::text) <= 65536",
            name="ck_access_review_campaigns_scope",
        ),
        sa.CheckConstraint(
            "scope_digest ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_campaigns_scope_digest",
        ),
        sa.CheckConstraint(
            "item_count BETWEEN 1 AND 10000",
            name="ck_access_review_campaigns_item_count",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_access_review_campaigns_revision"),
        sa.CheckConstraint(
            "review_due_at > snapshot_at",
            name="ck_access_review_campaigns_due_after_snapshot",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL AND close_reason IS NULL) OR "
            "(closed_at IS NOT NULL AND closed_by_email_snapshot IS NOT NULL "
            "AND close_reason IS NOT NULL)",
            name="ck_access_review_campaigns_close_bundle",
        ),
        sa.CheckConstraint(
            "(apply_started_at IS NULL AND apply_started_by_user_id IS NULL "
            "AND apply_started_by_email_snapshot IS NULL AND apply_run_id IS NULL) OR "
            "(apply_started_at IS NOT NULL "
            "AND apply_started_by_email_snapshot IS NOT NULL "
            "AND apply_run_id IS NOT NULL)",
            name="ck_access_review_campaigns_apply_bundle",
        ),
        sa.CheckConstraint(
            "(applied_at IS NULL AND applied_by_user_id IS NULL "
            "AND applied_by_email_snapshot IS NULL) OR "
            "(applied_at IS NOT NULL AND applied_by_email_snapshot IS NOT NULL)",
            name="ck_access_review_campaigns_applied_bundle",
        ),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_principal_type IS NULL "
            "AND cancelled_by_email_snapshot IS NULL AND cancel_reason IS NULL) OR "
            "(cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL AND "
            "((cancelled_by_principal_type = 'user' "
            "AND cancelled_by_email_snapshot IS NOT NULL) OR "
            "(cancelled_by_principal_type = 'system' "
            "AND cancelled_by_user_id IS NULL "
            "AND cancelled_by_email_snapshot IS NULL)))",
            name="ck_access_review_campaigns_cancel_bundle",
        ),
        sa.CheckConstraint(
            "(quarantined_at IS NULL AND quarantined_by_user_id IS NULL "
            "AND quarantined_by_principal_type IS NULL "
            "AND quarantined_by_email_snapshot IS NULL "
            "AND quarantine_reason IS NULL) OR "
            "(quarantined_at IS NOT NULL AND quarantine_reason IS NOT NULL AND "
            "((quarantined_by_principal_type = 'user' "
            "AND quarantined_by_email_snapshot IS NOT NULL) OR "
            "(quarantined_by_principal_type = 'system' "
            "AND quarantined_by_user_id IS NULL "
            "AND quarantined_by_email_snapshot IS NULL)))",
            name="ck_access_review_campaigns_quarantine_bundle",
        ),
        sa.CheckConstraint(
            "close_reason IS NULL OR (length(close_reason) BETWEEN 3 AND 2000 "
            "AND close_reason = btrim(close_reason))",
            name="ck_access_review_campaigns_close_reason",
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR (length(cancel_reason) BETWEEN 3 AND 2000 "
            "AND cancel_reason = btrim(cancel_reason))",
            name="ck_access_review_campaigns_cancel_reason",
        ),
        sa.CheckConstraint(
            "quarantine_reason IS NULL OR "
            "(length(quarantine_reason) BETWEEN 3 AND 2000 "
            "AND quarantine_reason = btrim(quarantine_reason))",
            name="ck_access_review_campaigns_quarantine_reason",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND closed_at IS NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'applying' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NOT NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'applied' AND closed_at IS NOT NULL "
            "AND apply_started_at IS NOT NULL AND applied_at IS NOT NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NULL) OR "
            "(status = 'cancelled' AND closed_at IS NULL "
            "AND apply_started_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NOT NULL AND quarantined_at IS NULL) OR "
            "(status = 'quarantined' AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND quarantined_at IS NOT NULL "
            "AND (apply_started_at IS NULL OR closed_at IS NOT NULL))",
            name="ck_access_review_campaigns_state",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["apply_started_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["quarantined_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "apply_run_id",
            name="uq_access_review_campaigns_apply_run_owner",
        ),
    )
    op.create_index(
        "ix_access_review_campaigns_status_due",
        "access_review_campaigns",
        ["status", "review_due_at"],
    )
    op.create_index(
        "ix_access_review_campaigns_created",
        "access_review_campaigns",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_access_review_campaigns_creator",
        "access_review_campaigns",
        ["created_by_user_id"],
    )

    op.create_table(
        "access_review_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_source", sa.String(length=16), nullable=False),
        sa.Column("assignment_revision_snapshot", sa.Integer(), nullable=True),
        sa.Column("assignment_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("principal_type", sa.String(length=24), nullable=False),
        sa.Column("principal_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("principal_label_snapshot", sa.String(length=320), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("target_key_snapshot", sa.String(length=255), nullable=False),
        sa.Column("target_label_snapshot", sa.String(length=320), nullable=False),
        sa.Column("target_revision_snapshot", sa.Integer(), nullable=False),
        sa.Column(
            "permissions_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "provenance_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "assignment_created_at_snapshot",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "access_expires_at_snapshot", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "item_type IN ('direct_user_role', 'legacy_user_role', "
            "'group_membership', "
            "'service_account_role', 'oidc_role_mapping', "
            "'oidc_group_mapping', 'live_elevation')",
            name="ck_access_review_items_type",
        ),
        sa.CheckConstraint(
            "principal_type IN ('user', 'service_account', 'oidc_provider')",
            name="ck_access_review_items_principal_type",
        ),
        sa.CheckConstraint(
            "target_type IN ('role', 'group')",
            name="ck_access_review_items_target_type",
        ),
        sa.CheckConstraint(
            "assignment_source IN ('local', 'legacy', 'oidc', 'temporary')",
            name="ck_access_review_items_source",
        ),
        sa.CheckConstraint(
            "(item_type IN ('direct_user_role', 'legacy_user_role', "
            "'group_membership', "
            "'live_elevation') AND principal_type = 'user') OR "
            "(item_type = 'service_account_role' "
            "AND principal_type = 'service_account') OR "
            "(item_type IN ('oidc_role_mapping', 'oidc_group_mapping') "
            "AND principal_type = 'oidc_provider')",
            name="ck_access_review_items_principal_matches_type",
        ),
        sa.CheckConstraint(
            "(item_type IN ('direct_user_role', 'legacy_user_role', "
            "'service_account_role', 'oidc_role_mapping', 'live_elevation') "
            "AND target_type = 'role') OR "
            "(item_type IN ('group_membership', 'oidc_group_mapping') "
            "AND target_type = 'group')",
            name="ck_access_review_items_target_matches_type",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_access_review_items_ordinal",
        ),
        sa.CheckConstraint(
            "assignment_revision_snapshot IS NULL OR assignment_revision_snapshot >= 1",
            name="ck_access_review_items_assignment_revision",
        ),
        sa.CheckConstraint(
            "target_revision_snapshot >= 1",
            name="ck_access_review_items_target_revision",
        ),
        sa.CheckConstraint(
            "assignment_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_items_fingerprint",
        ),
        sa.CheckConstraint(
            "principal_label_snapshot = btrim(principal_label_snapshot) "
            "AND length(principal_label_snapshot) BETWEEN 1 AND 320",
            name="ck_access_review_items_principal_label",
        ),
        sa.CheckConstraint(
            "target_key_snapshot = btrim(target_key_snapshot) "
            "AND length(target_key_snapshot) BETWEEN 1 AND 255",
            name="ck_access_review_items_target_key",
        ),
        sa.CheckConstraint(
            "target_label_snapshot = btrim(target_label_snapshot) "
            "AND length(target_label_snapshot) BETWEEN 1 AND 320",
            name="ck_access_review_items_target_label",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(permissions_snapshot) = 'array' "
            "AND octet_length(permissions_snapshot::text) <= 32768 "
            "AND NOT jsonb_path_exists(permissions_snapshot, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_access_review_items_permissions",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance_snapshot) = 'object' "
            "AND octet_length(provenance_snapshot::text) <= 65536",
            name="ck_access_review_items_provenance",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["access_review_campaigns.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "ordinal",
            name="uq_access_review_items_campaign_ordinal",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "item_type",
            "assignment_id",
            name="uq_access_review_items_campaign_assignment",
        ),
        sa.UniqueConstraint(
            "id",
            "campaign_id",
            "assignment_fingerprint",
            name="uq_access_review_items_decision_owner",
        ),
    )
    op.create_index(
        "ix_access_review_items_campaign_type_ordinal",
        "access_review_items",
        ["campaign_id", "item_type", "ordinal"],
    )
    op.create_index(
        "ix_access_review_items_principal",
        "access_review_items",
        ["principal_type", "principal_id_snapshot"],
    )
    op.create_index(
        "ix_access_review_items_target",
        "access_review_items",
        ["target_type", "target_id_snapshot"],
    )

    op.create_table(
        "access_review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("item_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('retain', 'revoke')",
            name="ck_access_review_decisions_value",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_access_review_decisions_sequence"),
        sa.CheckConstraint(
            "length(reason) BETWEEN 3 AND 2000 AND reason = btrim(reason)",
            name="ck_access_review_decisions_reason",
        ),
        sa.CheckConstraint(
            "item_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_decisions_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "campaign_id", "item_fingerprint"],
            [
                "access_review_items.id",
                "access_review_items.campaign_id",
                "access_review_items.assignment_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_decisions_item_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "sequence",
            name="uq_access_review_decisions_item_sequence",
        ),
        sa.UniqueConstraint(
            "id",
            "campaign_id",
            "item_id",
            "item_fingerprint",
            name="uq_access_review_decisions_receipt_owner",
        ),
    )
    op.create_index(
        "ix_access_review_decisions_campaign_item_sequence",
        "access_review_decisions",
        ["campaign_id", "item_id", "sequence"],
    )
    op.create_index(
        "ix_access_review_decisions_decider",
        "access_review_decisions",
        ["decided_by_user_id"],
    )

    op.create_table(
        "access_review_apply_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("item_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("apply_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("expected_assignment_revision", sa.Integer(), nullable=True),
        sa.Column("observed_assignment_revision", sa.Integer(), nullable=True),
        sa.Column("expected_target_revision", sa.Integer(), nullable=False),
        sa.Column("observed_target_revision", sa.Integer(), nullable=True),
        sa.Column("observed_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "mutation_performed", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("detail_code", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "result_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("applied_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("applied_by_email_snapshot", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('retained', 'revoked', 'already_absent', "
            "'manual_action_required', 'superseded', 'drifted', 'failed')",
            name="ck_access_review_apply_receipts_outcome",
        ),
        sa.CheckConstraint(
            "attempt >= 1", name="ck_access_review_apply_receipts_attempt"
        ),
        sa.CheckConstraint(
            "item_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_apply_receipts_item_fingerprint",
        ),
        sa.CheckConstraint(
            "observed_fingerprint IS NULL OR observed_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_access_review_apply_receipts_observed_fingerprint",
        ),
        sa.CheckConstraint(
            "expected_assignment_revision IS NULL OR expected_assignment_revision >= 1",
            name="ck_access_review_apply_receipts_expected_assignment_revision",
        ),
        sa.CheckConstraint(
            "observed_assignment_revision IS NULL OR observed_assignment_revision >= 1",
            name="ck_access_review_apply_receipts_observed_assignment_revision",
        ),
        sa.CheckConstraint(
            "expected_target_revision >= 1",
            name="ck_access_review_apply_receipts_expected_target_revision",
        ),
        sa.CheckConstraint(
            "observed_target_revision IS NULL OR observed_target_revision >= 1",
            name="ck_access_review_apply_receipts_observed_target_revision",
        ),
        sa.CheckConstraint(
            "(outcome = 'revoked' AND mutation_performed) OR "
            "(outcome <> 'revoked' AND NOT mutation_performed)",
            name="ck_access_review_apply_receipts_mutation_outcome",
        ),
        sa.CheckConstraint(
            "detail_code ~ '^[a-z][a-z0-9_]{2,63}$'",
            name="ck_access_review_apply_receipts_detail_code",
        ),
        sa.CheckConstraint(
            "length(detail) BETWEEN 3 AND 2000 AND detail = btrim(detail)",
            name="ck_access_review_apply_receipts_detail",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object' "
            "AND octet_length(result_snapshot::text) <= 65536",
            name="ck_access_review_apply_receipts_result",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "apply_run_id"],
            [
                "access_review_campaigns.id",
                "access_review_campaigns.apply_run_id",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_apply_receipts_campaign_run",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "campaign_id", "item_id", "item_fingerprint"],
            [
                "access_review_decisions.id",
                "access_review_decisions.campaign_id",
                "access_review_decisions.item_id",
                "access_review_decisions.item_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_access_review_apply_receipts_decision",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "attempt",
            name="uq_access_review_apply_receipts_item_attempt",
        ),
    )
    op.create_index(
        "ix_access_review_apply_receipts_campaign_item_attempt",
        "access_review_apply_receipts",
        ["campaign_id", "item_id", "attempt"],
    )
    op.create_index(
        "ix_access_review_apply_receipts_run",
        "access_review_apply_receipts",
        ["apply_run_id", "created_at"],
    )
    op.create_index(
        "ix_access_review_apply_receipts_actor",
        "access_review_apply_receipts",
        ["applied_by_user_id"],
    )

    _create_evidence_triggers()


def _create_evidence_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION access_review_validate_campaign_item_count()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            observed_item_count integer;
        BEGIN
            SELECT count(*)
              INTO observed_item_count
              FROM access_review_items
             WHERE campaign_id = NEW.id;

            IF observed_item_count <> NEW.item_count THEN
                RAISE EXCEPTION
                    'access-review campaign item_count % does not match % persisted items',
                    NEW.item_count, observed_item_count
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_access_review_campaigns_exact_item_count';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_item_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            campaign_record record;
            observed_item_count integer;
        BEGIN
            FOR campaign_record IN
                SELECT campaigns.id, campaigns.status, campaigns.item_count
                  FROM access_review_campaigns AS campaigns
                  JOIN (
                      SELECT DISTINCT campaign_id
                        FROM access_review_new_items
                  ) AS affected ON affected.campaign_id = campaigns.id
                  FOR SHARE OF campaigns
            LOOP
                IF campaign_record.status <> 'open' THEN
                    RAISE EXCEPTION
                        'access-review items may be added only while a campaign is open'
                        USING ERRCODE = '55000';
                END IF;

                IF EXISTS (
                    SELECT 1
                      FROM access_review_new_items AS inserted
                     WHERE inserted.campaign_id = campaign_record.id
                       AND inserted.ordinal > campaign_record.item_count
                ) THEN
                    RAISE EXCEPTION
                        'access-review item ordinal exceeds the campaign item_count'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_access_review_campaigns_exact_item_count';
                END IF;

                SELECT count(*)
                  INTO observed_item_count
                  FROM access_review_items
                 WHERE campaign_id = campaign_record.id;
                IF observed_item_count > campaign_record.item_count THEN
                    RAISE EXCEPTION
                        'access-review campaign has more items than its sealed item_count'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_access_review_campaigns_exact_item_count';
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_decision_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            campaign_status text;
        BEGIN
            SELECT status
              INTO campaign_status
              FROM access_review_campaigns
             WHERE id = NEW.campaign_id
               FOR SHARE;

            IF campaign_status IS NULL THEN
                RAISE EXCEPTION 'access-review decision campaign does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF campaign_status <> 'open' THEN
                RAISE EXCEPTION
                    'access-review decisions may be added only while a campaign is open'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_receipt_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            campaign_status text;
            campaign_run_id uuid;
            latest_decision_id uuid;
            latest_attempt integer;
            latest_outcome text;
        BEGIN
            SELECT status, apply_run_id
              INTO campaign_status, campaign_run_id
              FROM access_review_campaigns
             WHERE id = NEW.campaign_id
               FOR SHARE;

            IF campaign_status IS NULL THEN
                RAISE EXCEPTION 'access-review receipt campaign does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF campaign_status <> 'applying' THEN
                RAISE EXCEPTION
                    'access-review receipts may be added only while a campaign is applying'
                    USING ERRCODE = '55000';
            END IF;
            IF campaign_run_id IS DISTINCT FROM NEW.apply_run_id THEN
                RAISE EXCEPTION
                    'access-review receipt apply run does not match its campaign'
                    USING ERRCODE = '23503',
                          CONSTRAINT = 'fk_access_review_apply_receipts_campaign_run';
            END IF;

            PERFORM 1
              FROM access_review_items
             WHERE id = NEW.item_id
               AND campaign_id = NEW.campaign_id
               FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'access-review receipt item does not belong to its campaign'
                    USING ERRCODE = '23503';
            END IF;

            SELECT id
              INTO latest_decision_id
              FROM access_review_decisions
             WHERE item_id = NEW.item_id
               AND campaign_id = NEW.campaign_id
             ORDER BY sequence DESC
             LIMIT 1;
            IF latest_decision_id IS DISTINCT FROM NEW.decision_id THEN
                RAISE EXCEPTION
                    'access-review receipt must reference the latest item decision'
                    USING ERRCODE = '23503',
                          CONSTRAINT = 'access_review_latest_decision';
            END IF;

            SELECT attempt, outcome
              INTO latest_attempt, latest_outcome
              FROM access_review_apply_receipts
             WHERE item_id = NEW.item_id
             ORDER BY attempt DESC
             LIMIT 1;
            IF latest_outcome IN (
                'retained', 'revoked', 'already_absent', 'superseded'
            ) THEN
                RAISE EXCEPTION
                    'access-review receipt cannot follow a terminal apply result'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.attempt <> COALESCE(latest_attempt, 0) + 1 THEN
                RAISE EXCEPTION
                    'access-review receipt attempts must be contiguous'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'access_review_contiguous_attempts';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_reject_history_rewrite()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'access-review history is append-only'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'access-review history rows are immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_campaign_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            observed_decision_count integer;
            observed_terminal_count integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'access-review campaigns cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.name IS DISTINCT FROM OLD.name
               OR NEW.description IS DISTINCT FROM OLD.description
               OR NEW.scope_snapshot IS DISTINCT FROM OLD.scope_snapshot
               OR NEW.scope_digest IS DISTINCT FROM OLD.scope_digest
               OR NEW.snapshot_at IS DISTINCT FROM OLD.snapshot_at
               OR NEW.review_due_at IS DISTINCT FROM OLD.review_due_at
               OR NEW.item_count IS DISTINCT FROM OLD.item_count
               OR NEW.created_by_email_snapshot
                    IS DISTINCT FROM OLD.created_by_email_snapshot
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'access-review campaign snapshots are immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
               AND NOT (OLD.created_by_user_id IS NOT NULL
                        AND NEW.created_by_user_id IS NULL) THEN
                RAISE EXCEPTION 'campaign creator references may only be cleared'
                    USING ERRCODE = '55000';
            END IF;

            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NOT (
                    (OLD.status = 'open' AND NEW.status IN
                        ('closed', 'cancelled', 'quarantined'))
                    OR (OLD.status = 'closed' AND NEW.status IN
                        ('applying', 'quarantined'))
                    OR (OLD.status = 'applying' AND NEW.status IN
                        ('applied', 'quarantined'))
                ) THEN
                    RAISE EXCEPTION 'invalid access-review campaign transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.revision <> OLD.revision + 1 THEN
                    RAISE EXCEPTION 'campaign transitions must increment revision once'
                        USING ERRCODE = '55000';
                END IF;

                IF OLD.status = 'open' AND NEW.status = 'closed' THEN
                    SELECT count(DISTINCT item_id)
                      INTO observed_decision_count
                      FROM access_review_decisions
                     WHERE campaign_id = NEW.id;
                    IF observed_decision_count <> NEW.item_count THEN
                        RAISE EXCEPTION
                            'access-review campaign cannot close with incomplete decisions'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'access_review_decisions_complete';
                    END IF;
                END IF;

                IF OLD.status = 'applying' AND NEW.status = 'applied' THEN
                    SELECT count(*)
                      INTO observed_terminal_count
                      FROM (
                          SELECT DISTINCT ON (item_id) item_id, outcome
                            FROM access_review_apply_receipts
                           WHERE campaign_id = NEW.id
                           ORDER BY item_id, attempt DESC
                      ) AS latest_receipts
                     WHERE outcome IN (
                         'retained', 'revoked', 'already_absent', 'superseded'
                     );
                    IF observed_terminal_count <> NEW.item_count THEN
                        RAISE EXCEPTION
                            'access-review campaign cannot complete with unresolved apply results'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'access_review_apply_complete';
                    END IF;
                END IF;
            ELSIF NEW.revision IS DISTINCT FROM OLD.revision
                  AND NOT (OLD.status = 'open'
                           AND NEW.revision = OLD.revision + 1) THEN
                RAISE EXCEPTION 'only open decision batches may advance revision without a state transition'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.closed_at IS NOT NULL AND (
                NEW.closed_at IS DISTINCT FROM OLD.closed_at
                OR NEW.closed_by_email_snapshot
                    IS DISTINCT FROM OLD.closed_by_email_snapshot
                OR NEW.close_reason IS DISTINCT FROM OLD.close_reason
                OR (NEW.closed_by_user_id IS DISTINCT FROM OLD.closed_by_user_id
                    AND NOT (OLD.closed_by_user_id IS NOT NULL
                             AND NEW.closed_by_user_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'campaign close evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.apply_started_at IS NOT NULL AND (
                NEW.apply_started_at IS DISTINCT FROM OLD.apply_started_at
                OR NEW.apply_run_id IS DISTINCT FROM OLD.apply_run_id
                OR NEW.apply_started_by_email_snapshot
                    IS DISTINCT FROM OLD.apply_started_by_email_snapshot
                OR (NEW.apply_started_by_user_id
                        IS DISTINCT FROM OLD.apply_started_by_user_id
                    AND NOT (OLD.apply_started_by_user_id IS NOT NULL
                             AND NEW.apply_started_by_user_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'campaign apply-start evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.applied_at IS NOT NULL AND (
                NEW.applied_at IS DISTINCT FROM OLD.applied_at
                OR NEW.applied_by_email_snapshot
                    IS DISTINCT FROM OLD.applied_by_email_snapshot
                OR (NEW.applied_by_user_id IS DISTINCT FROM OLD.applied_by_user_id
                    AND NOT (OLD.applied_by_user_id IS NOT NULL
                             AND NEW.applied_by_user_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'campaign apply evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.cancelled_at IS NOT NULL AND (
                NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                OR NEW.cancelled_by_principal_type
                    IS DISTINCT FROM OLD.cancelled_by_principal_type
                OR NEW.cancelled_by_email_snapshot
                    IS DISTINCT FROM OLD.cancelled_by_email_snapshot
                OR NEW.cancel_reason IS DISTINCT FROM OLD.cancel_reason
                OR (NEW.cancelled_by_user_id
                        IS DISTINCT FROM OLD.cancelled_by_user_id
                    AND NOT (OLD.cancelled_by_user_id IS NOT NULL
                             AND NEW.cancelled_by_user_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'campaign cancellation evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.quarantined_at IS NOT NULL AND (
                NEW.quarantined_at IS DISTINCT FROM OLD.quarantined_at
                OR NEW.quarantined_by_principal_type
                    IS DISTINCT FROM OLD.quarantined_by_principal_type
                OR NEW.quarantined_by_email_snapshot
                    IS DISTINCT FROM OLD.quarantined_by_email_snapshot
                OR NEW.quarantine_reason IS DISTINCT FROM OLD.quarantine_reason
                OR (NEW.quarantined_by_user_id
                        IS DISTINCT FROM OLD.quarantined_by_user_id
                    AND NOT (OLD.quarantined_by_user_id IS NOT NULL
                             AND NEW.quarantined_by_user_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'campaign quarantine evidence is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_decision_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'access-review decisions are append-only'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
               OR NEW.item_id IS DISTINCT FROM OLD.item_id
               OR NEW.item_fingerprint IS DISTINCT FROM OLD.item_fingerprint
               OR NEW.sequence IS DISTINCT FROM OLD.sequence
               OR NEW.decision IS DISTINCT FROM OLD.decision
               OR NEW.decided_by_email_snapshot
                    IS DISTINCT FROM OLD.decided_by_email_snapshot
               OR NEW.reason IS DISTINCT FROM OLD.reason
               OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
               OR (NEW.decided_by_user_id IS DISTINCT FROM OLD.decided_by_user_id
                   AND NOT (OLD.decided_by_user_id IS NOT NULL
                            AND NEW.decided_by_user_id IS NULL)) THEN
                RAISE EXCEPTION 'access-review decisions are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION access_review_guard_receipt_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'access-review apply receipts are append-only'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
               OR NEW.item_id IS DISTINCT FROM OLD.item_id
               OR NEW.item_fingerprint IS DISTINCT FROM OLD.item_fingerprint
               OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
               OR NEW.apply_run_id IS DISTINCT FROM OLD.apply_run_id
               OR NEW.attempt IS DISTINCT FROM OLD.attempt
               OR NEW.outcome IS DISTINCT FROM OLD.outcome
               OR NEW.expected_assignment_revision
                    IS DISTINCT FROM OLD.expected_assignment_revision
               OR NEW.observed_assignment_revision
                    IS DISTINCT FROM OLD.observed_assignment_revision
               OR NEW.expected_target_revision
                    IS DISTINCT FROM OLD.expected_target_revision
               OR NEW.observed_target_revision
                    IS DISTINCT FROM OLD.observed_target_revision
               OR NEW.observed_fingerprint IS DISTINCT FROM OLD.observed_fingerprint
               OR NEW.mutation_performed IS DISTINCT FROM OLD.mutation_performed
               OR NEW.detail_code IS DISTINCT FROM OLD.detail_code
               OR NEW.detail IS DISTINCT FROM OLD.detail
               OR NEW.result_snapshot IS DISTINCT FROM OLD.result_snapshot
               OR NEW.applied_by_email_snapshot
                    IS DISTINCT FROM OLD.applied_by_email_snapshot
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR (NEW.applied_by_user_id IS DISTINCT FROM OLD.applied_by_user_id
                   AND NOT (OLD.applied_by_user_id IS NOT NULL
                            AND NEW.applied_by_user_id IS NULL)) THEN
                RAISE EXCEPTION 'access-review apply receipts are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_access_review_campaign_item_count
        AFTER INSERT ON access_review_campaigns
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION access_review_validate_campaign_item_count()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_item_insert_guard
        AFTER INSERT ON access_review_items
        REFERENCING NEW TABLE AS access_review_new_items
        FOR EACH STATEMENT EXECUTE FUNCTION access_review_guard_item_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_decision_insert_guard
        BEFORE INSERT ON access_review_decisions
        FOR EACH ROW EXECUTE FUNCTION access_review_guard_decision_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_receipt_insert_guard
        BEFORE INSERT ON access_review_apply_receipts
        FOR EACH ROW EXECUTE FUNCTION access_review_guard_receipt_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_campaign_history
        BEFORE UPDATE OR DELETE ON access_review_campaigns
        FOR EACH ROW EXECUTE FUNCTION access_review_guard_campaign_history()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_item_history
        BEFORE UPDATE OR DELETE ON access_review_items
        FOR EACH ROW EXECUTE FUNCTION access_review_reject_history_rewrite()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_decision_history
        BEFORE UPDATE OR DELETE ON access_review_decisions
        FOR EACH ROW EXECUTE FUNCTION access_review_guard_decision_history()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_review_receipt_history
        BEFORE UPDATE OR DELETE ON access_review_apply_receipts
        FOR EACH ROW EXECUTE FUNCTION access_review_guard_receipt_history()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(bind.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0)
        for table in (
            "access_review_campaigns",
            "access_review_items",
            "access_review_decisions",
            "access_review_apply_receipts",
        )
    }
    populated = [f"{table}={count}" for table, count in counts.items() if count]
    if populated:
        raise RuntimeError(
            "Cannot downgrade access-review persistence while immutable governance "
            f"history would be lost ({', '.join(populated)}). Export the campaign "
            "history, deliberately TRUNCATE the four access-review tables, and retry."
        )

    op.execute(
        "DROP TRIGGER trg_access_review_receipt_insert_guard "
        "ON access_review_apply_receipts"
    )
    op.execute(
        "DROP TRIGGER trg_access_review_receipt_history ON access_review_apply_receipts"
    )
    op.execute(
        "DROP TRIGGER trg_access_review_decision_insert_guard "
        "ON access_review_decisions"
    )
    op.execute(
        "DROP TRIGGER trg_access_review_decision_history ON access_review_decisions"
    )
    op.execute(
        "DROP TRIGGER trg_access_review_item_insert_guard ON access_review_items"
    )
    op.execute("DROP TRIGGER trg_access_review_item_history ON access_review_items")
    op.execute(
        "DROP TRIGGER trg_access_review_campaign_item_count ON access_review_campaigns"
    )
    op.execute(
        "DROP TRIGGER trg_access_review_campaign_history ON access_review_campaigns"
    )
    op.execute("DROP FUNCTION access_review_guard_receipt_history()")
    op.execute("DROP FUNCTION access_review_guard_decision_history()")
    op.execute("DROP FUNCTION access_review_guard_campaign_history()")
    op.execute("DROP FUNCTION access_review_reject_history_rewrite()")
    op.execute("DROP FUNCTION access_review_guard_receipt_insert()")
    op.execute("DROP FUNCTION access_review_guard_decision_insert()")
    op.execute("DROP FUNCTION access_review_guard_item_insert()")
    op.execute("DROP FUNCTION access_review_validate_campaign_item_count()")

    op.drop_index(
        "ix_access_review_apply_receipts_actor",
        table_name="access_review_apply_receipts",
    )
    op.drop_index(
        "ix_access_review_apply_receipts_run",
        table_name="access_review_apply_receipts",
    )
    op.drop_index(
        "ix_access_review_apply_receipts_campaign_item_attempt",
        table_name="access_review_apply_receipts",
    )
    op.drop_table("access_review_apply_receipts")
    op.drop_index(
        "ix_access_review_decisions_decider",
        table_name="access_review_decisions",
    )
    op.drop_index(
        "ix_access_review_decisions_campaign_item_sequence",
        table_name="access_review_decisions",
    )
    op.drop_table("access_review_decisions")
    op.drop_index("ix_access_review_items_target", table_name="access_review_items")
    op.drop_index("ix_access_review_items_principal", table_name="access_review_items")
    op.drop_index(
        "ix_access_review_items_campaign_type_ordinal",
        table_name="access_review_items",
    )
    op.drop_table("access_review_items")
    op.drop_index(
        "ix_access_review_campaigns_creator",
        table_name="access_review_campaigns",
    )
    op.drop_index(
        "ix_access_review_campaigns_created",
        table_name="access_review_campaigns",
    )
    op.drop_index(
        "ix_access_review_campaigns_status_due",
        table_name="access_review_campaigns",
    )
    op.drop_table("access_review_campaigns")
