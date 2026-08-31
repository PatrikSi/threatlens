"""govern dynamic action-approval targets and retained receipts

Revision ID: 0080_action_approval_policy
Revises: 0079_metric_captured_taint
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0080_action_approval_policy"
down_revision = "0079_metric_captured_taint"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = "00000000-0000-4000-8000-000000000202"
_FENCE_FUNCTION = "threatlens_fence_action_approval_policy_v1"
_FENCE_TRIGGER = "trg_action_approval_requests_policy_fence_v1"


def upgrade() -> None:
    bind = op.get_bind()
    policy_revision = _require_disabled_data_policy(bind, operation="migrate")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                data_policy_state,
                handling_labels,
                ai_task_runs,
                ai_provider_attempt_receipts,
                action_approval_requests,
                action_execution_receipts,
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
            "Cannot migrate action approvals because the quarantine handling "
            "label is missing or inactive."
        )

    _add_scope_columns()
    _classify_legacy_approvals(bind)
    _create_approval_envelopes(bind, policy_revision=policy_revision)
    _install_rolling_writer_fence(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _require_disabled_data_policy(bind, operation="downgrade")
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                data_policy_state,
                action_approval_requests,
                data_access_envelopes,
                data_access_envelope_sources,
                data_access_envelope_labels
            IN ACCESS EXCLUSIVE MODE
            """
        )
    )
    _drop_rolling_writer_fence(bind)
    bind.execute(
        sa.text(
            "DELETE FROM data_access_envelopes "
            "WHERE resource_type = 'action_approval'"
        )
    )
    op.drop_index(
        "ix_action_approval_requests_data_access_source",
        table_name="action_approval_requests",
    )
    op.drop_constraint(
        "ck_action_approval_requests_system_scope",
        "action_approval_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_action_approval_requests_data_access_source",
        "action_approval_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_action_approval_requests_data_access_source_type",
        "action_approval_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_action_approval_requests_data_access_scope",
        "action_approval_requests",
        type_="check",
    )
    op.drop_constraint(
        "ck_action_approval_requests_target_policy_version",
        "action_approval_requests",
        type_="check",
    )
    op.drop_column("action_approval_requests", "data_access_source_id")
    op.drop_column("action_approval_requests", "data_access_source_type")
    op.drop_column("action_approval_requests", "data_access_lineage_complete")
    op.drop_column("action_approval_requests", "data_access_scope")
    op.drop_column("action_approval_requests", "target_data_policy_version")


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
            f"Cannot {operation} action approvals because data policy state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} action approvals while data policy audit or "
            "enforcement is active. Disable data policy and reset coverage first."
        )
    return int(state["revision"])


def _add_scope_columns() -> None:
    op.add_column(
        "action_approval_requests",
        sa.Column(
            "target_data_policy_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "action_approval_requests",
        sa.Column(
            "data_access_scope",
            sa.String(length=16),
            nullable=False,
            server_default="governed",
        ),
    )
    op.add_column(
        "action_approval_requests",
        sa.Column(
            "data_access_lineage_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "action_approval_requests",
        sa.Column(
            "data_access_source_type",
            sa.String(length=32),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.add_column(
        "action_approval_requests",
        sa.Column("data_access_source_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_check_constraint(
        "ck_action_approval_requests_target_policy_version",
        "action_approval_requests",
        "target_data_policy_version >= 1",
    )
    op.create_check_constraint(
        "ck_action_approval_requests_data_access_scope",
        "action_approval_requests",
        "data_access_scope IN ('system', 'governed')",
    )
    op.create_check_constraint(
        "ck_action_approval_requests_data_access_source_type",
        "action_approval_requests",
        "data_access_source_type IN "
        "('system_control_plane', 'ai_task_run', 'unresolved')",
    )
    op.create_check_constraint(
        "ck_action_approval_requests_data_access_source",
        "action_approval_requests",
        "(data_access_source_type = 'ai_task_run' "
        "AND data_access_source_id IS NOT NULL) OR "
        "(data_access_source_type <> 'ai_task_run' "
        "AND data_access_source_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_action_approval_requests_system_scope",
        "action_approval_requests",
        "data_access_scope <> 'system' OR "
        "(data_access_lineage_complete AND "
        "action_definition_version = 1 AND "
        "target_data_policy_version = 1 AND "
        "((data_access_source_type = 'system_control_plane' "
        "AND ((action_type = 'service_account.disable' "
        "AND target_type = 'service_account') OR "
        "(action_type = 'iam.role.delete' AND target_type = 'iam_role'))) "
        "OR (data_access_source_type = 'ai_task_run' "
        "AND action_type IN "
        "('ai.provider_attempt.confirm_not_sent', "
        "'ai.provider_attempt.acknowledge_may_have_sent') "
        "AND target_type = 'ai_provider_attempt_receipt')))",
    )
    op.create_index(
        "ix_action_approval_requests_data_access_source",
        "action_approval_requests",
        ["data_access_source_type", "data_access_source_id"],
    )


def _classify_legacy_approvals(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE action_approval_requests AS approval
            SET data_access_scope = 'system',
                data_access_lineage_complete = true,
                data_access_source_type = 'system_control_plane',
                data_access_source_id = NULL
            WHERE (
                    (approval.action_type = 'service_account.disable'
                     AND approval.target_type = 'service_account')
                 OR (approval.action_type = 'iam.role.delete'
                     AND approval.target_type = 'iam_role')
                  )
              AND approval.action_definition_version = 1
              AND NOT EXISTS (
                  SELECT 1 FROM data_access_envelopes AS envelope
                  WHERE envelope.resource_type = 'action_approval'
                    AND envelope.resource_id = approval.id
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE action_approval_requests AS approval
            SET data_access_source_type = 'ai_task_run',
                data_access_source_id = receipt.task_run_id_snapshot,
                data_access_scope = CASE
                    WHEN run.id IS NOT NULL
                     AND run.data_access_scope = 'system'
                     AND run.data_access_lineage_complete
                     AND run.task_type = 'connection_test'
                     AND run.item_id IS NULL
                     AND run.daily_brief_id IS NULL
                     AND run.report_id IS NULL
                     AND run.parent_run_id IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM data_access_envelopes AS run_envelope
                         WHERE run_envelope.resource_type = 'ai_task_run'
                           AND run_envelope.resource_id = run.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM data_access_envelopes AS approval_envelope
                         WHERE approval_envelope.resource_type = 'action_approval'
                           AND approval_envelope.resource_id = approval.id
                     )
                    THEN 'system'
                    ELSE 'governed'
                END,
                data_access_lineage_complete = CASE
                    WHEN run.id IS NOT NULL
                     AND run.data_access_scope = 'system'
                     AND run.data_access_lineage_complete
                     AND run.task_type = 'connection_test'
                     AND run.item_id IS NULL
                     AND run.daily_brief_id IS NULL
                     AND run.report_id IS NULL
                     AND run.parent_run_id IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM data_access_envelopes AS run_envelope
                         WHERE run_envelope.resource_type = 'ai_task_run'
                           AND run_envelope.resource_id = run.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM data_access_envelopes AS approval_envelope
                         WHERE approval_envelope.resource_type = 'action_approval'
                           AND approval_envelope.resource_id = approval.id
                     )
                    THEN true
                    ELSE false
                END
            FROM ai_provider_attempt_receipts AS receipt
            LEFT JOIN ai_task_runs AS run
              ON run.id = receipt.task_run_id_snapshot
            WHERE receipt.id::text = approval.target_id
              AND approval.target_type = 'ai_provider_attempt_receipt'
              AND approval.action_type IN (
                  'ai.provider_attempt.confirm_not_sent',
                  'ai.provider_attempt.acknowledge_may_have_sent'
              )
              AND approval.action_definition_version = 1
            """
        )
    )


def _create_approval_envelopes(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelopes (
                id, resource_type, resource_id, source_count,
                policy_revision, created_at, updated_at
            )
            SELECT md5('0080:action_approval:' || approval.id::text)::uuid,
                   'action_approval', approval.id, 0, :policy_revision,
                   approval.created_at, now()
            FROM action_approval_requests AS approval
            WHERE approval.data_access_scope = 'governed'
            ON CONFLICT (resource_type, resource_id) DO NOTHING
            """
        ),
        {"policy_revision": policy_revision},
    )
    _quarantine_preexisting_sources(bind, policy_revision=policy_revision)
    _copy_valid_task_run_sources(bind)
    _quarantine_unresolved_approvals(bind, policy_revision=policy_revision)
    _rebuild_approval_envelopes(bind, policy_revision=policy_revision)
    bind.execute(
        sa.text(
            """
            UPDATE action_approval_requests
            SET data_access_lineage_complete = true
            WHERE data_access_scope = 'governed'
            """
        )
    )


def _quarantine_preexisting_sources(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_sources (
                id, envelope_id, source_type, source_id, source_version,
                source_feed_id, source_parent_id, handling_label_id,
                captured_policy_revision, source_digest, captured_at
            )
            SELECT md5('0080:preexisting:' || envelope.id::text)::uuid,
                   envelope.id, 'unresolved', approval.id::text,
                   'migrate:0080:preexisting', NULL, NULL, :quarantine,
                   :policy_revision, NULL, approval.created_at
            FROM data_access_envelopes AS envelope
            JOIN action_approval_requests AS approval
              ON approval.id = envelope.resource_id
            WHERE envelope.resource_type = 'action_approval'
              AND envelope.id <> md5(
                  '0080:action_approval:' || approval.id::text
              )::uuid
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "policy_revision": policy_revision,
            "quarantine": _QUARANTINE_LABEL_ID,
        },
    )


def _copy_valid_task_run_sources(bind) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_sources (
                id, envelope_id, source_type, source_id, source_version,
                source_feed_id, source_parent_id, handling_label_id,
                captured_policy_revision, source_digest, captured_at
            )
            SELECT md5(
                       '0080:copied:' || approval.id::text || ':' || source.id::text
                   )::uuid,
                   target_envelope.id, source.source_type, source.source_id,
                   source.source_version,
                   source.source_feed_id, source.id,
                   source.handling_label_id, source.captured_policy_revision,
                   source.source_digest, source.captured_at
            FROM action_approval_requests AS approval
            JOIN ai_task_runs AS run
              ON run.id = approval.data_access_source_id
             AND run.data_access_scope = 'governed'
             AND run.data_access_lineage_complete
            JOIN data_access_envelopes AS source_envelope
              ON source_envelope.resource_type = 'ai_task_run'
             AND source_envelope.resource_id = run.id
            JOIN data_access_envelope_sources AS source
              ON source.envelope_id = source_envelope.id
            JOIN data_access_envelopes AS target_envelope
              ON target_envelope.resource_type = 'action_approval'
             AND target_envelope.resource_id = approval.id
            WHERE approval.data_access_scope = 'governed'
              AND approval.data_access_source_type = 'ai_task_run'
              AND source_envelope.source_count > 0
              AND source_envelope.source_count = (
                  SELECT count(*)
                  FROM data_access_envelope_sources AS counted_source
                  WHERE counted_source.envelope_id = source_envelope.id
              )
              AND source_envelope.source_count = (
                  SELECT COALESCE(sum(counted_label.source_count), 0)
                  FROM data_access_envelope_labels AS counted_label
                  WHERE counted_label.envelope_id = source_envelope.id
              )
              AND source_envelope.policy_revision >= (
                  SELECT max(revision_source.captured_policy_revision)
                  FROM data_access_envelope_sources AS revision_source
                  WHERE revision_source.envelope_id = source_envelope.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM data_access_envelope_sources AS invalid_source
                  LEFT JOIN handling_labels AS active_label
                    ON active_label.id = invalid_source.handling_label_id
                   AND active_label.is_active
                  WHERE invalid_source.envelope_id = source_envelope.id
                    AND active_label.id IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM (
                      SELECT source_count.handling_label_id AS label_id,
                             count(*)::integer AS source_total
                      FROM data_access_envelope_sources AS source_count
                      WHERE source_count.envelope_id = source_envelope.id
                      GROUP BY source_count.handling_label_id
                  ) AS source_totals
                  FULL JOIN (
                      SELECT label_count.label_id,
                             label_count.source_count AS label_total
                      FROM data_access_envelope_labels AS label_count
                      WHERE label_count.envelope_id = source_envelope.id
                  ) AS label_totals USING (label_id)
                  WHERE source_totals.source_total IS DISTINCT FROM
                        label_totals.label_total
              )
            ON CONFLICT DO NOTHING
            """
        )
    )


def _quarantine_unresolved_approvals(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO data_access_envelope_sources (
                id, envelope_id, source_type, source_id, source_version,
                source_feed_id, source_parent_id, handling_label_id,
                captured_policy_revision, source_digest, captured_at
            )
            SELECT md5('0080:unresolved:' || approval.id::text)::uuid,
                   envelope.id, 'unresolved', approval.id::text,
                   'migrate:0080:unresolved', NULL, NULL, :quarantine,
                   :policy_revision, NULL, approval.created_at
            FROM action_approval_requests AS approval
            JOIN data_access_envelopes AS envelope
              ON envelope.resource_type = 'action_approval'
             AND envelope.resource_id = approval.id
            WHERE approval.data_access_scope = 'governed'
              AND NOT EXISTS (
                  SELECT 1 FROM data_access_envelope_sources AS copied
                  WHERE copied.envelope_id = envelope.id
                    AND copied.source_parent_id IS NOT NULL
              )
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "policy_revision": policy_revision,
            "quarantine": _QUARANTINE_LABEL_ID,
        },
    )


def _rebuild_approval_envelopes(bind, *, policy_revision: int) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM data_access_envelope_labels AS label
            USING data_access_envelopes AS envelope
            WHERE label.envelope_id = envelope.id
              AND envelope.resource_type = 'action_approval'
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
             AND envelope.resource_type = 'action_approval'
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
                 AND envelope.resource_type = 'action_approval'
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


def _install_rolling_writer_fence(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_FENCE_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                active_coverage integer;
                valid_lineage boolean;
                target_lineage_locked boolean;
            BEGIN
                SELECT coverage_version INTO active_coverage
                FROM data_policy_state
                WHERE id = 1
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'Action approval capture requires data policy state.'
                        USING ERRCODE = '55000';
                END IF;
                valid_lineage := false;
                target_lineage_locked := false;
                IF active_coverage > 0 AND NEW.data_access_lineage_complete THEN
                    IF NEW.data_access_source_type = 'ai_task_run'
                       AND NEW.data_access_source_id IS NOT NULL THEN
                        -- Keep the global run -> receipt order used by provider
                        -- reservation and retention. These locks serialize the
                        -- exact target binding with concurrent lineage deletion.
                        PERFORM run.id
                        FROM ai_task_runs AS run
                        WHERE run.id = NEW.data_access_source_id
                        FOR KEY SHARE;
                        IF FOUND THEN
                            PERFORM receipt.id
                            FROM ai_provider_attempt_receipts AS receipt
                            WHERE receipt.id::text = NEW.target_id
                              AND receipt.task_run_id_snapshot =
                                  NEW.data_access_source_id
                            FOR KEY SHARE;
                            target_lineage_locked := FOUND;
                        END IF;
                    END IF;
                    IF NEW.data_access_scope = 'system' THEN
                        valid_lineage :=
                            NEW.action_definition_version = 1
                            AND NEW.target_data_policy_version = 1
                            AND NOT EXISTS (
                                SELECT 1 FROM data_access_envelopes AS envelope
                                WHERE envelope.resource_type = 'action_approval'
                                  AND envelope.resource_id = NEW.id
                            )
                            AND (
                                (
                                    NEW.data_access_source_type =
                                        'system_control_plane'
                                    AND (
                                        (
                                            NEW.action_type =
                                                'service_account.disable'
                                            AND NEW.target_type = 'service_account'
                                        )
                                        OR (
                                            NEW.action_type = 'iam.role.delete'
                                            AND NEW.target_type = 'iam_role'
                                        )
                                    )
                                )
                                OR (
                                    NEW.data_access_source_type = 'ai_task_run'
                                    AND target_lineage_locked
                                    AND NEW.action_type IN (
                                        'ai.provider_attempt.confirm_not_sent',
                                        'ai.provider_attempt.acknowledge_may_have_sent'
                                    )
                                    AND NEW.target_type =
                                        'ai_provider_attempt_receipt'
                                    AND EXISTS (
                                        SELECT 1
                                        FROM ai_provider_attempt_receipts AS receipt
                                        JOIN ai_task_runs AS run
                                          ON run.id = receipt.task_run_id_snapshot
                                        WHERE receipt.id::text = NEW.target_id
                                          AND receipt.task_run_id_snapshot =
                                              NEW.data_access_source_id
                                          AND run.data_access_scope = 'system'
                                          AND run.data_access_lineage_complete
                                          AND run.task_type = 'connection_test'
                                          AND run.item_id IS NULL
                                          AND run.daily_brief_id IS NULL
                                          AND run.report_id IS NULL
                                          AND run.parent_run_id IS NULL
                                          AND NOT EXISTS (
                                              SELECT 1
                                              FROM data_access_envelopes AS run_envelope
                                              WHERE run_envelope.resource_type =
                                                    'ai_task_run'
                                                AND run_envelope.resource_id = run.id
                                          )
                                    )
                                )
                            );
                    ELSIF NEW.data_access_scope = 'governed' THEN
                        valid_lineage := EXISTS (
                            SELECT 1
                            FROM data_access_envelopes AS envelope
                            WHERE envelope.resource_type = 'action_approval'
                              AND envelope.resource_id = NEW.id
                              AND envelope.source_count > 0
                              AND envelope.source_count = (
                                  SELECT count(*)
                                  FROM data_access_envelope_sources AS source
                                  WHERE source.envelope_id = envelope.id
                              )
                              AND envelope.source_count = (
                                  SELECT COALESCE(sum(label.source_count), 0)
                                  FROM data_access_envelope_labels AS label
                                  WHERE label.envelope_id = envelope.id
                              )
                              AND envelope.policy_revision >= (
                                  SELECT max(source.captured_policy_revision)
                                  FROM data_access_envelope_sources AS source
                                  WHERE source.envelope_id = envelope.id
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM data_access_envelope_sources AS source
                                  LEFT JOIN handling_labels AS label
                                    ON label.id = source.handling_label_id
                                   AND label.is_active
                                  WHERE source.envelope_id = envelope.id
                                    AND label.id IS NULL
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM (
                                      SELECT source.handling_label_id AS label_id,
                                             count(*)::integer AS source_total
                                      FROM data_access_envelope_sources AS source
                                      WHERE source.envelope_id = envelope.id
                                      GROUP BY source.handling_label_id
                                  ) AS source_totals
                                  FULL JOIN (
                                      SELECT label.label_id,
                                             label.source_count AS label_total
                                      FROM data_access_envelope_labels AS label
                                      WHERE label.envelope_id = envelope.id
                                  ) AS label_totals USING (label_id)
                                  WHERE source_totals.source_total IS DISTINCT FROM
                                        label_totals.label_total
                              )
                              AND (
                                  EXISTS (
                                      SELECT 1
                                      FROM data_access_envelope_sources AS quarantine
                                      WHERE quarantine.envelope_id = envelope.id
                                        AND quarantine.source_parent_id IS NULL
                                        AND quarantine.source_type = 'unresolved'
                                        AND quarantine.handling_label_id =
                                            '{_QUARANTINE_LABEL_ID}'::uuid
                                  )
                                  OR (
                                      NEW.action_definition_version = 1
                                      AND NEW.target_data_policy_version = 1
                                      AND NEW.data_access_source_type = 'ai_task_run'
                                      AND target_lineage_locked
                                      AND NEW.action_type IN (
                                          'ai.provider_attempt.confirm_not_sent',
                                          'ai.provider_attempt.acknowledge_may_have_sent'
                                      )
                                      AND NEW.target_type =
                                          'ai_provider_attempt_receipt'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM ai_provider_attempt_receipts AS receipt
                                          WHERE receipt.id::text = NEW.target_id
                                            AND receipt.task_run_id_snapshot =
                                                NEW.data_access_source_id
                                      )
                                      AND EXISTS (
                                          SELECT 1
                                          FROM ai_task_runs AS governed_run
                                          WHERE governed_run.id =
                                                NEW.data_access_source_id
                                            AND governed_run.data_access_scope =
                                                'governed'
                                            AND governed_run.data_access_lineage_complete
                                      )
                                      AND NOT EXISTS (
                                          SELECT 1
                                          FROM data_access_envelope_sources AS child
                                          WHERE child.envelope_id = envelope.id
                                            AND NOT (
                                                EXISTS (
                                                    SELECT 1
                                                    FROM data_access_envelope_sources AS parent
                                                    JOIN data_access_envelopes AS parent_envelope
                                                      ON parent_envelope.id =
                                                         parent.envelope_id
                                                    WHERE parent.id =
                                                          child.source_parent_id
                                                      AND parent_envelope.resource_type =
                                                          'ai_task_run'
                                                      AND parent_envelope.resource_id =
                                                          NEW.data_access_source_id
                                                      AND child.source_type =
                                                          parent.source_type
                                                      AND child.source_id = parent.source_id
                                                      AND child.source_version =
                                                          parent.source_version
                                                      AND child.source_feed_id IS NOT DISTINCT FROM
                                                          parent.source_feed_id
                                                      AND child.handling_label_id =
                                                          parent.handling_label_id
                                                      AND child.captured_policy_revision =
                                                          parent.captured_policy_revision
                                                      AND child.source_digest IS NOT DISTINCT FROM
                                                          parent.source_digest
                                                      AND child.captured_at = parent.captured_at
                                                )
                                                OR (
                                                    child.source_parent_id IS NULL
                                                    AND child.source_type = 'feed_taint'
                                                    AND EXISTS (
                                                        SELECT 1
                                                        FROM data_access_envelope_sources AS run_taint
                                                        JOIN data_access_envelopes AS run_envelope
                                                          ON run_envelope.id =
                                                             run_taint.envelope_id
                                                        WHERE run_envelope.resource_type =
                                                              'ai_task_run'
                                                          AND run_envelope.resource_id =
                                                              NEW.data_access_source_id
                                                          AND run_taint.source_type =
                                                              child.source_type
                                                          AND run_taint.source_id =
                                                              child.source_id
                                                          AND run_taint.source_version =
                                                              child.source_version
                                                          AND run_taint.source_feed_id IS NOT DISTINCT FROM
                                                              child.source_feed_id
                                                          AND run_taint.handling_label_id =
                                                              child.handling_label_id
                                                          AND run_taint.captured_policy_revision =
                                                              child.captured_policy_revision
                                                          AND run_taint.source_digest IS NOT DISTINCT FROM
                                                              child.source_digest
                                                    )
                                                )
                                                OR (
                                                    child.source_parent_id IS NULL
                                                    AND child.source_type = 'unresolved'
                                                    AND child.handling_label_id =
                                                        '{_QUARANTINE_LABEL_ID}'::uuid
                                                )
                                            )
                                      )
                                      AND NOT EXISTS (
                                          SELECT 1
                                          FROM data_access_envelope_sources AS run_source
                                          JOIN data_access_envelopes AS run_envelope
                                            ON run_envelope.id = run_source.envelope_id
                                          WHERE run_envelope.resource_type =
                                                'ai_task_run'
                                            AND run_envelope.resource_id =
                                                NEW.data_access_source_id
                                            AND NOT EXISTS (
                                                SELECT 1
                                                FROM data_access_envelope_sources AS child
                                                WHERE child.envelope_id = envelope.id
                                                  AND (
                                                      child.source_parent_id =
                                                          run_source.id
                                                      OR (
                                                          run_source.source_type =
                                                              'feed_taint'
                                                          AND child.source_parent_id
                                                              IS NULL
                                                          AND child.source_type =
                                                              run_source.source_type
                                                          AND child.source_id =
                                                              run_source.source_id
                                                          AND child.source_version =
                                                              run_source.source_version
                                                          AND child.source_feed_id
                                                              IS NOT DISTINCT FROM
                                                              run_source.source_feed_id
                                                          AND child.handling_label_id =
                                                              run_source.handling_label_id
                                                          AND child.captured_policy_revision =
                                                              run_source.captured_policy_revision
                                                          AND child.source_digest
                                                              IS NOT DISTINCT FROM
                                                              run_source.source_digest
                                                      )
                                                  )
                                            )
                                      )
                                  )
                              )
                        );
                    END IF;
                END IF;
                IF active_coverage > 0 AND NOT valid_lineage THEN
                    RAISE EXCEPTION
                        'Action approval target provenance is incomplete or inconsistent.'
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
            CREATE TRIGGER {_FENCE_TRIGGER}
            BEFORE INSERT OR UPDATE ON action_approval_requests
            FOR EACH ROW
            EXECUTE FUNCTION {_FENCE_FUNCTION}()
            """
        )
    )


def _drop_rolling_writer_fence(bind) -> None:
    bind.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS {_FENCE_TRIGGER} "
            "ON action_approval_requests"
        )
    )
    bind.execute(sa.text(f"DROP FUNCTION IF EXISTS {_FENCE_FUNCTION}()"))
