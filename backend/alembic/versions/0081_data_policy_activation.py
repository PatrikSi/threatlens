"""activate complete application data-policy coverage

Revision ID: 0081_data_policy_activation
Revises: 0080_action_approval_policy
Create Date: 2026-08-31
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import sqlalchemy as sa
from alembic import op


revision = "0081_data_policy_activation"
down_revision = "0080_action_approval_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    _require_policy_state(bind, operation="activate", expected_coverage=0)
    _lock_governed_data(bind)
    blockers = _database_blockers(bind)
    if blockers:
        rendered = ", ".join(
            f"{code}={count}" for code, count in sorted(blockers.items())
        )
        raise RuntimeError(
            "Cannot activate complete data-policy coverage because retained "
            f"database integrity checks failed: {rendered}"
        )
    updated = bind.execute(
        sa.text(
            "UPDATE data_policy_state SET coverage_version = 1, "
            "updated_at = now() WHERE id = 1 AND mode = 'disabled' "
            "AND coverage_version = 0"
        )
    )
    if updated.rowcount != 1:
        raise RuntimeError(
            "Data policy state changed while complete coverage was activated."
        )


def downgrade() -> None:
    bind = op.get_bind()
    _require_policy_state(bind, operation="downgrade", expected_coverage=1)
    updated = bind.execute(
        sa.text(
            "UPDATE data_policy_state SET coverage_version = 0, "
            "updated_at = now() WHERE id = 1 AND mode = 'disabled' "
            "AND coverage_version = 1"
        )
    )
    if updated.rowcount != 1:
        raise RuntimeError(
            "Data policy state changed while complete coverage was downgraded."
        )


def _require_policy_state(
    bind,
    *,
    operation: str,
    expected_coverage: int,
) -> None:
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
            f"Cannot {operation} data-policy coverage because policy state is missing."
        )
    if state["mode"] != "disabled":
        raise RuntimeError(
            f"Cannot {operation} data-policy coverage while audit or enforcement is active."
        )
    if int(state["coverage_version"]) != expected_coverage:
        raise RuntimeError(
            f"Cannot {operation} data-policy coverage from coverage version "
            f"{state['coverage_version']}; expected {expected_coverage}."
        )


def _lock_governed_data(bind) -> None:
    bind.execute(
        sa.text(
            """
            LOCK TABLE
                handling_labels,
                data_policy_role_grants,
                feeds,
                data_access_envelopes,
                data_access_envelope_sources,
                data_access_envelope_labels,
                reports,
                ai_daily_briefs,
                investigations,
                alert_occurrences,
                integration_events,
                integration_deliveries,
                ai_task_runs,
                ai_usage_events,
                ai_provider_attempt_receipts,
                audit_logs,
                audit_log_data_access_labels,
                audit_log_data_access_feeds,
                alert_occurrence_metrics,
                alert_occurrence_metric_cohorts,
                alert_occurrence_metric_cohort_labels,
                alert_occurrence_metric_cohort_captured_labels,
                alert_occurrence_metric_cohort_taint_labels,
                integration_delivery_metrics,
                integration_delivery_metric_cohorts,
                integration_delivery_metric_cohort_labels,
                integration_delivery_metric_cohort_captured_labels,
                integration_delivery_metric_cohort_taint_labels,
                integration_delivery_metric_cohort_feeds,
                action_approval_requests
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )


def _database_blockers(bind):
    blockers: dict[str, int] = {}

    def add(code: str, count: int | None) -> None:
        normalized = int(count or 0)
        if normalized:
            blockers[code] = normalized

    add("built_in_labels_invalid", _invalid_builtin_label_count(bind))
    add(
        "feeds_use_inactive_labels",
        _count(
            bind,
            """
            SELECT count(*)
            FROM feeds AS feed
            JOIN handling_labels AS label ON label.id = feed.handling_label_id
            WHERE NOT label.is_active
            """,
        ),
    )
    add(
        "restricted_labels_missing_admin_grant",
        _count(
            bind,
            """
            SELECT count(*)
            FROM handling_labels AS label
            WHERE label.is_active AND NOT label.is_unrestricted
              AND NOT EXISTS (
                  SELECT 1 FROM data_policy_role_grants AS grant_row
                  WHERE grant_row.label_id = label.id
                    AND grant_row.role_id =
                        '00000000-0000-4000-8000-000000000001'::uuid
              )
            """,
        ),
    )
    add(
        "restricted_labels_without_roles",
        _count(
            bind,
            """
            SELECT count(*)
            FROM handling_labels AS label
            WHERE label.is_active AND NOT label.is_unrestricted
              AND NOT EXISTS (
                  SELECT 1 FROM data_policy_role_grants AS grant_row
                  WHERE grant_row.label_id = label.id
              )
            """,
        ),
    )
    add(
        "unsupported_envelope_resource_types",
        _count(
            bind,
            """
            SELECT count(*) FROM data_access_envelopes
            WHERE resource_type NOT IN (
                'report', 'ai_daily_brief', 'investigation',
                'alert_occurrence', 'integration_event',
                'integration_delivery', 'ai_task_run', 'ai_usage_event',
                'action_approval'
            )
            """,
        ),
    )
    add("envelope_lineage_parity_invalid", _envelope_mismatch_count(bind))
    add("governed_resources_missing_envelopes", _missing_envelope_count(bind))
    ai_runs, ai_usage = _ai_scope_mismatch_counts(bind)
    add("ai_task_run_scope_integrity_invalid", ai_runs)
    add("ai_usage_event_scope_integrity_invalid", ai_usage)
    add("normalized_audit_lineage_invalid", _audit_mismatch_count(bind))
    add(
        "inactive_normalized_label_references",
        _inactive_label_reference_count(bind),
    )
    for code, count in _metric_mismatch_counts(bind).items():
        add(code, count)
    add(
        "action_approval_data_policy_invalid",
        _action_approval_mismatch_count(bind),
    )
    return blockers


def _invalid_builtin_label_count(bind) -> int:
    expected = (
        (
            "00000000-0000-4000-8000-000000000201",
            "unrestricted",
            True,
        ),
        (
            "00000000-0000-4000-8000-000000000202",
            "quarantine",
            False,
        ),
    )
    invalid = 0
    for label_id, key, unrestricted in expected:
        valid = bind.scalar(
            sa.text(
                "SELECT key = :key AND is_unrestricted = :unrestricted "
                "AND is_system AND is_active FROM handling_labels "
                "WHERE id = CAST(:label_id AS uuid)"
            ),
            {
                "key": key,
                "label_id": label_id,
                "unrestricted": unrestricted,
            },
        )
        invalid += int(valid is not True)
    return invalid


def _envelope_mismatch_count(bind) -> int:
    return _count(
        bind,
        """
        WITH source_totals AS (
            SELECT envelope_id, count(*)::integer AS source_count,
                   max(captured_policy_revision) AS max_revision
            FROM data_access_envelope_sources GROUP BY envelope_id
        ),
        label_totals AS (
            SELECT envelope_id, sum(source_count)::integer AS label_count
            FROM data_access_envelope_labels GROUP BY envelope_id
        ),
        label_mismatches AS (
            SELECT COALESCE(source.envelope_id, label.envelope_id) AS envelope_id
            FROM (
                SELECT envelope_id, handling_label_id AS label_id,
                       count(*)::integer AS source_count
                FROM data_access_envelope_sources
                GROUP BY envelope_id, handling_label_id
            ) AS source
            FULL JOIN data_access_envelope_labels AS label
              ON label.envelope_id = source.envelope_id
             AND label.label_id = source.label_id
            WHERE source.source_count IS DISTINCT FROM label.source_count
        )
        SELECT count(*)
        FROM data_access_envelopes AS envelope
        LEFT JOIN source_totals AS source ON source.envelope_id = envelope.id
        LEFT JOIN label_totals AS label ON label.envelope_id = envelope.id
        WHERE envelope.source_count IS DISTINCT FROM
                  COALESCE(source.source_count, 0)
           OR envelope.source_count IS DISTINCT FROM
                  COALESCE(label.label_count, 0)
           OR envelope.policy_revision < COALESCE(source.max_revision, 1)
           OR EXISTS (
               SELECT 1 FROM label_mismatches AS mismatch
               WHERE mismatch.envelope_id = envelope.id
           )
        """,
    )


def _missing_envelope_count(bind) -> int:
    return _count(
        bind,
        """
        SELECT count(*) FROM (
            SELECT id FROM reports
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'report' AND resource_id = reports.id
            )
            UNION ALL
            SELECT id FROM ai_daily_briefs
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'ai_daily_brief'
                  AND resource_id = ai_daily_briefs.id
            )
            UNION ALL
            SELECT id FROM investigations
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'investigation'
                  AND resource_id = investigations.id
            )
            UNION ALL
            SELECT id FROM alert_occurrences
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'alert_occurrence'
                  AND resource_id = alert_occurrences.id
            )
            UNION ALL
            SELECT id FROM integration_events
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'integration_event'
                  AND resource_id = integration_events.id
            )
            UNION ALL
            SELECT id FROM integration_deliveries
            WHERE NOT EXISTS (
                SELECT 1 FROM data_access_envelopes
                WHERE resource_type = 'integration_delivery'
                  AND resource_id = integration_deliveries.id
            )
        ) AS missing
        """,
    )


def _ai_scope_mismatch_counts(bind) -> tuple[int, int]:
    invalid_runs = _count(
        bind,
        """
        SELECT count(*) FROM ai_task_runs AS run
        WHERE NOT (
            (
                run.data_access_scope = 'system'
                AND run.task_type = 'connection_test'
                AND run.data_access_lineage_complete
                AND run.item_id IS NULL AND run.daily_brief_id IS NULL
                AND run.report_id IS NULL AND run.parent_run_id IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'ai_task_run'
                      AND envelope.resource_id = run.id
                )
            ) OR (
                run.data_access_scope = 'governed'
                AND run.data_access_lineage_complete
                AND EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'ai_task_run'
                      AND envelope.resource_id = run.id
                )
            )
        )
        """,
    )
    invalid_usage = _count(
        bind,
        """
        SELECT count(*) FROM ai_usage_events AS usage
        WHERE NOT (
            (
                usage.data_access_scope = 'system'
                AND usage.feature_type = 'connection_test'
                AND usage.item_id IS NULL AND usage.daily_brief_id IS NULL
                AND usage.report_id IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'ai_usage_event'
                      AND envelope.resource_id = usage.id
                )
                AND (
                    usage.task_run_id_snapshot IS NULL OR EXISTS (
                        SELECT 1 FROM ai_task_runs AS run
                        WHERE run.id = usage.task_run_id_snapshot
                          AND run.data_access_scope = 'system'
                          AND run.task_type = 'connection_test'
                          AND run.data_access_lineage_complete
                          AND run.item_id IS NULL
                          AND run.daily_brief_id IS NULL
                          AND run.report_id IS NULL
                          AND run.parent_run_id IS NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM data_access_envelopes AS envelope
                              WHERE envelope.resource_type = 'ai_task_run'
                                AND envelope.resource_id = run.id
                          )
                    )
                )
            ) OR (
                usage.data_access_scope = 'governed'
                AND EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'ai_usage_event'
                      AND envelope.resource_id = usage.id
                )
            )
        )
        """,
    )
    return invalid_runs, invalid_usage


def _audit_mismatch_count(bind) -> int:
    return _count(
        bind,
        r"""
        WITH base_invalid AS (
            SELECT audit.id AS audit_log_id FROM audit_logs AS audit
            WHERE (
                audit.data_access_governed AND NOT EXISTS (
                    SELECT 1 FROM audit_log_data_access_labels AS label
                    WHERE label.audit_log_id = audit.id
                )
            ) OR (
                NOT audit.data_access_governed AND EXISTS (
                    SELECT 1 FROM audit_log_data_access_labels AS label
                    WHERE label.audit_log_id = audit.id
                )
            )
        ),
        linked_envelopes AS (
            SELECT audit.id AS audit_log_id, envelope.id AS envelope_id
            FROM audit_logs AS audit
            JOIN data_access_envelopes AS envelope
              ON envelope.resource_type = CASE
                  WHEN audit.resource_type = 'daily_brief'
                  THEN 'ai_daily_brief' ELSE audit.resource_type
              END
             AND envelope.resource_id = CASE
                 WHEN COALESCE(audit.resource_id, '') ~
                      '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                 THEN audit.resource_id::uuid ELSE NULL
             END
            UNION
            SELECT audit.id, envelope.id
            FROM audit_logs AS audit
            JOIN data_access_envelopes AS envelope
              ON envelope.resource_type = 'ai_task_run'
             AND envelope.resource_id = CASE
                 WHEN audit.resource_type = 'ai_task_run'
                  AND COALESCE(audit.resource_id, '') ~
                      '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                 THEN audit.resource_id::uuid
                 WHEN COALESCE(audit.metadata_json->>'run_id', '') ~
                      '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                 THEN (audit.metadata_json->>'run_id')::uuid ELSE NULL
             END
            WHERE audit.action LIKE 'ai.%'
               OR audit.action LIKE 'reports.generate.%'
        ),
        invalid_legacy AS (
            SELECT DISTINCT audit.id AS audit_log_id
            FROM audit_logs AS audit
            CROSS JOIN LATERAL jsonb_array_elements_text(
                audit.data_access_label_ids
            ) AS value(label_id)
            LEFT JOIN handling_labels AS handling
              ON handling.id = CASE
                 WHEN value.label_id ~
                      '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                 THEN value.label_id::uuid ELSE NULL
             END
            WHERE audit.data_access_governed
              AND (
                  (
                      handling.id IS NOT NULL AND NOT EXISTS (
                          SELECT 1 FROM audit_log_data_access_labels AS label
                          WHERE label.audit_log_id = audit.id
                            AND label.label_id = handling.id
                      )
                  ) OR (
                      handling.id IS NULL AND NOT EXISTS (
                          SELECT 1 FROM audit_log_data_access_labels AS label
                          WHERE label.audit_log_id = audit.id
                            AND label.label_id =
                                '00000000-0000-4000-8000-000000000202'::uuid
                      )
                  )
              )
        ),
        invalid_linked AS (
            SELECT linked.audit_log_id
            FROM linked_envelopes AS linked
            JOIN data_access_envelope_labels AS expected
              ON expected.envelope_id = linked.envelope_id
            WHERE NOT EXISTS (
                SELECT 1 FROM audit_log_data_access_labels AS actual
                WHERE actual.audit_log_id = linked.audit_log_id
                  AND actual.label_id = expected.label_id
            )
            UNION
            SELECT linked.audit_log_id
            FROM linked_envelopes AS linked
            JOIN data_access_envelope_sources AS expected
              ON expected.envelope_id = linked.envelope_id
             AND expected.source_feed_id IS NOT NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM audit_log_data_access_feeds AS actual
                WHERE actual.audit_log_id = linked.audit_log_id
                  AND actual.source_feed_id_snapshot = expected.source_feed_id
            )
        )
        SELECT count(*) FROM (
            SELECT audit_log_id FROM base_invalid
            UNION SELECT audit_log_id FROM invalid_legacy
            UNION SELECT audit_log_id FROM invalid_linked
        ) AS invalid
        """,
    )


def _inactive_label_reference_count(bind) -> int:
    return _count(
        bind,
        """
        SELECT count(*) FROM (
            SELECT source.handling_label_id AS label_id
            FROM data_access_envelope_sources AS source
            UNION ALL SELECT label_id FROM data_access_envelope_labels
            UNION ALL SELECT label_id FROM audit_log_data_access_labels
            UNION ALL SELECT label_id
                FROM alert_occurrence_metric_cohort_captured_labels
            UNION ALL SELECT label_id
                FROM alert_occurrence_metric_cohort_taint_labels
            UNION ALL SELECT label_id FROM alert_occurrence_metric_cohort_labels
            UNION ALL SELECT label_id
                FROM integration_delivery_metric_cohort_captured_labels
            UNION ALL SELECT label_id
                FROM integration_delivery_metric_cohort_taint_labels
            UNION ALL SELECT label_id
                FROM integration_delivery_metric_cohort_labels
        ) AS reference
        JOIN handling_labels AS label ON label.id = reference.label_id
        WHERE NOT label.is_active
        """,
    )


def _metric_mismatch_counts(bind) -> dict[str, int]:
    counts: dict[str, int] = {}
    alert_invalid = _cohort_identity_mismatch_count(
        bind,
        cohort_table="alert_occurrence_metric_cohorts",
        captured_table="alert_occurrence_metric_cohort_captured_labels",
        taint_table="alert_occurrence_metric_cohort_taint_labels",
        effective_table="alert_occurrence_metric_cohort_labels",
        integration=False,
    )
    if alert_invalid:
        counts["alert_metric_cohort_integrity_invalid"] = alert_invalid
    integration_invalid = _cohort_identity_mismatch_count(
        bind,
        cohort_table="integration_delivery_metric_cohorts",
        captured_table="integration_delivery_metric_cohort_captured_labels",
        taint_table="integration_delivery_metric_cohort_taint_labels",
        effective_table="integration_delivery_metric_cohort_labels",
        integration=True,
    )
    if integration_invalid:
        counts["integration_metric_cohort_integrity_invalid"] = integration_invalid
    alert_parity = _count(
        bind,
        """
        SELECT count(*) FROM alert_occurrence_metrics AS metric
        LEFT JOIN (
            SELECT metric_id, sum(occurrence_count) AS occurrence_count
            FROM alert_occurrence_metric_cohorts GROUP BY metric_id
        ) AS cohort ON cohort.metric_id = metric.id
        WHERE metric.occurrence_count <> COALESCE(cohort.occurrence_count, 0)
        """,
    )
    if alert_parity:
        counts["alert_metric_aggregate_parity_invalid"] = alert_parity
    integration_parity = _count(
        bind,
        """
        SELECT count(*) FROM integration_delivery_metrics AS metric
        LEFT JOIN (
            SELECT metric_id,
                   sum(succeeded_count) AS succeeded_count,
                   sum(failed_count) AS failed_count,
                   sum(dead_letter_count) AS dead_letter_count,
                   sum(attempt_count) AS attempt_count,
                   sum(duration_total_ms) AS duration_total_ms,
                   max(duration_max_ms) AS duration_max_ms
            FROM integration_delivery_metric_cohorts GROUP BY metric_id
        ) AS cohort ON cohort.metric_id = metric.id
        WHERE metric.succeeded_count <> COALESCE(cohort.succeeded_count, 0)
           OR metric.failed_count <> COALESCE(cohort.failed_count, 0)
           OR metric.dead_letter_count <>
                  COALESCE(cohort.dead_letter_count, 0)
           OR metric.attempt_count <> COALESCE(cohort.attempt_count, 0)
           OR metric.duration_total_ms <>
                  COALESCE(cohort.duration_total_ms, 0)
           OR metric.duration_max_ms <> COALESCE(cohort.duration_max_ms, 0)
        """,
    )
    if integration_parity:
        counts["integration_metric_aggregate_parity_invalid"] = integration_parity
    return counts


def _cohort_identity_mismatch_count(
    bind,
    *,
    cohort_table: str,
    captured_table: str,
    taint_table: str,
    effective_table: str,
    integration: bool,
) -> int:
    captured = _cohort_values(bind, captured_table, "label_id")
    taints = _cohort_values(bind, taint_table, "label_id")
    effective = _cohort_values(bind, effective_table, "label_id")
    feeds = (
        _cohort_values(
            bind,
            "integration_delivery_metric_cohort_feeds",
            "source_feed_id_snapshot",
        )
        if integration
        else defaultdict(set)
    )
    columns = (
        "id, policy_cohort_key, captured_policy_revision, "
        "provenance_complete, source_count"
        if integration
        else "id, policy_cohort_key, captured_policy_revision, provenance_complete"
    )
    rows = bind.execute(sa.text(f"SELECT {columns} FROM {cohort_table}")).mappings()
    quarantine = "00000000-0000-4000-8000-000000000202"
    invalid = 0
    for row in rows:
        cohort_id = row["id"]
        captured_labels = captured[cohort_id]
        if integration:
            canonical = (
                f"{max(1, int(row['captured_policy_revision']))}:"
                f"{int(bool(row['provenance_complete']))}:"
                f"{max(0, int(row['source_count']))}:"
                f"{'|'.join(sorted(captured_labels))}:"
                f"{'|'.join(sorted(feeds[cohort_id]))}"
            )
        else:
            canonical = (
                f"{max(0, int(row['captured_policy_revision']))}:"
                f"{'|'.join(sorted(captured_labels))}"
            )
        expected = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        invalid += int(
            not captured_labels
            or expected != row["policy_cohort_key"]
            or effective[cohort_id] != captured_labels | taints[cohort_id]
            or (not row["provenance_complete"] and quarantine not in captured_labels)
        )
    return invalid


def _cohort_values(bind, table: str, value_column: str):
    values: defaultdict[object, set[str]] = defaultdict(set)
    rows = bind.execute(
        sa.text(f"SELECT cohort_id, {value_column} AS value FROM {table}")
    )
    for cohort_id, value in rows:
        values[cohort_id].add(str(value))
    return values


def _action_approval_mismatch_count(bind) -> int:
    return _count(
        bind,
        """
        SELECT count(*) FROM action_approval_requests AS approval
        WHERE NOT (
            (
                approval.data_access_scope = 'system'
                AND approval.data_access_lineage_complete
                AND approval.target_data_policy_version = 1
                AND NOT EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'action_approval'
                      AND envelope.resource_id = approval.id
                )
                AND (
                    (
                        approval.data_access_source_type =
                            'system_control_plane'
                        AND approval.data_access_source_id IS NULL
                        AND (
                            (
                                approval.action_type =
                                    'service_account.disable'
                                AND approval.action_definition_version = 1
                                AND approval.target_type = 'service_account'
                            ) OR (
                                approval.action_type = 'iam.role.delete'
                                AND approval.action_definition_version = 1
                                AND approval.target_type = 'iam_role'
                            )
                        )
                    ) OR (
                        approval.data_access_source_type = 'ai_task_run'
                        AND approval.data_access_source_id IS NOT NULL
                        AND approval.action_definition_version = 1
                        AND approval.action_type IN (
                            'ai.provider_attempt.confirm_not_sent',
                            'ai.provider_attempt.acknowledge_may_have_sent'
                        )
                        AND approval.target_type =
                            'ai_provider_attempt_receipt'
                        AND EXISTS (
                            SELECT 1
                            FROM ai_provider_attempt_receipts AS receipt
                            JOIN ai_task_runs AS run
                              ON run.id = receipt.task_run_id_snapshot
                            WHERE receipt.id::text = approval.target_id
                              AND run.id = approval.data_access_source_id
                              AND run.data_access_scope = 'system'
                              AND run.task_type = 'connection_test'
                              AND run.data_access_lineage_complete
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
                )
            ) OR (
                approval.data_access_scope = 'governed'
                AND approval.data_access_lineage_complete
                AND EXISTS (
                    SELECT 1 FROM data_access_envelopes AS envelope
                    WHERE envelope.resource_type = 'action_approval'
                      AND envelope.resource_id = approval.id
                      AND envelope.source_count > 0
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM data_access_envelope_sources AS quarantine
                              WHERE quarantine.envelope_id = envelope.id
                                AND quarantine.source_parent_id IS NULL
                                AND quarantine.source_type = 'unresolved'
                                AND quarantine.handling_label_id =
                                    '00000000-0000-4000-8000-000000000202'::uuid
                          ) OR (
                              approval.action_definition_version = 1
                              AND approval.target_data_policy_version = 1
                              AND approval.data_access_source_type =
                                  'ai_task_run'
                              AND approval.data_access_source_id IS NOT NULL
                              AND approval.action_type IN (
                                  'ai.provider_attempt.confirm_not_sent',
                                  'ai.provider_attempt.acknowledge_may_have_sent'
                              )
                              AND approval.target_type =
                                  'ai_provider_attempt_receipt'
                              AND EXISTS (
                                  SELECT 1
                                  FROM ai_provider_attempt_receipts AS receipt
                                  WHERE receipt.id::text = approval.target_id
                                    AND receipt.task_run_id_snapshot =
                                        approval.data_access_source_id
                              )
                              AND EXISTS (
                                  SELECT 1
                                  FROM ai_task_runs AS governed_run
                                  WHERE governed_run.id =
                                        approval.data_access_source_id
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
                                                  approval.data_access_source_id
                                              AND child.source_type =
                                                  parent.source_type
                                              AND child.source_id = parent.source_id
                                              AND child.source_version =
                                                  parent.source_version
                                              AND child.source_feed_id
                                                  IS NOT DISTINCT FROM
                                                  parent.source_feed_id
                                              AND child.handling_label_id =
                                                  parent.handling_label_id
                                              AND child.captured_policy_revision =
                                                  parent.captured_policy_revision
                                              AND child.source_digest
                                                  IS NOT DISTINCT FROM
                                                  parent.source_digest
                                              AND child.captured_at =
                                                  parent.captured_at
                                        ) OR (
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
                                                      approval.data_access_source_id
                                                  AND run_taint.source_type =
                                                      child.source_type
                                                  AND run_taint.source_id =
                                                      child.source_id
                                                  AND run_taint.source_version =
                                                      child.source_version
                                                  AND run_taint.source_feed_id
                                                      IS NOT DISTINCT FROM
                                                      child.source_feed_id
                                                  AND run_taint.handling_label_id =
                                                      child.handling_label_id
                                                  AND run_taint.captured_policy_revision =
                                                      child.captured_policy_revision
                                                  AND run_taint.source_digest
                                                      IS NOT DISTINCT FROM
                                                      child.source_digest
                                            )
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
                                        approval.data_access_source_id
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM data_access_envelope_sources AS child
                                        WHERE child.envelope_id = envelope.id
                                          AND (
                                              child.source_parent_id = run_source.id
                                              OR (
                                                  run_source.source_type =
                                                      'feed_taint'
                                                  AND child.source_parent_id IS NULL
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
                )
            )
        )
        """,
    )


def _count(bind, statement: str) -> int:
    return int(bind.scalar(sa.text(statement)) or 0)


__all__ = ["downgrade", "upgrade"]
