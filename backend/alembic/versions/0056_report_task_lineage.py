"""add explicit report task supersession lineage

Revision ID: 0056_report_task_lineage
Revises: 0055_schedule_version_guard
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0056_report_task_lineage"
down_revision = "0055_schedule_version_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _relation_schema("reports")
    op.add_column(
        "ai_task_runs",
        sa.Column("superseded_by_task_run_id", sa.Uuid(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_ai_task_runs_superseded_by_task_run_id",
        "ai_task_runs",
        "ai_task_runs",
        ["superseded_by_task_run_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_ai_task_runs_superseded_by_task_run_id"),
        "ai_task_runs",
        ["superseded_by_task_run_id"],
        unique=False,
        schema=schema,
    )
    op.add_column(
        "reports",
        sa.Column("initial_task_run_id", sa.Uuid(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_reports_initial_task_run_id_ai_task_runs",
        "reports",
        "ai_task_runs",
        ["initial_task_run_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_reports_initial_task_run_id"),
        "reports",
        ["initial_task_run_id"],
        unique=False,
        schema=schema,
    )

    qualified_schema = _quote_identifier(schema)
    op.execute(
        sa.text(
            f"""
            UPDATE {qualified_schema}.ai_task_runs
            SET task_type = 'report'
            WHERE task_type = 'report_superseded'
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {qualified_schema}.ai_task_runs AS legacy
            SET superseded_by_task_run_id = replacement.id
            FROM {qualified_schema}.ai_task_runs AS replacement
            WHERE legacy.superseded_by_task_run_id IS NULL
              AND legacy.task_type = 'report'
              AND replacement.task_type = 'report'
              AND replacement.report_id = legacy.report_id
              AND legacy.metadata_json ->> 'superseded_by_task_run_id' = replacement.id::text
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH first_queued_runs AS (
                SELECT DISTINCT ON (run.report_id)
                    run.report_id,
                    run.id AS task_run_id
                FROM {qualified_schema}.ai_task_runs AS run
                JOIN {qualified_schema}.ai_task_events AS event
                  ON event.task_run_id = run.id
                 AND event.event_type = 'queued'
                JOIN {qualified_schema}.reports AS origin_report
                  ON origin_report.id = run.report_id
                WHERE run.report_id IS NOT NULL
                  AND run.task_type = 'report'
                  AND (
                      run.metadata_json ->> 'report_request_origin' = 'true'
                      OR (
                          run.metadata_json ->> 'report_request_origin' IS NULL
                          AND event.created_at = origin_report.created_at
                      )
                  )
                ORDER BY run.report_id,
                    COALESCE(
                        run.metadata_json ->> 'report_request_origin' = 'true',
                        false
                    ) DESC,
                    event.created_at ASC, event.id ASC, run.id ASC
            ), request_runs AS (
                SELECT first.report_id,
                    COALESCE(replacement.id, first.task_run_id) AS task_run_id
                FROM first_queued_runs AS first
                JOIN {qualified_schema}.ai_task_runs AS original
                  ON original.id = first.task_run_id
                LEFT JOIN {qualified_schema}.ai_task_runs AS replacement
                  ON replacement.id = original.superseded_by_task_run_id
                 AND replacement.report_id = first.report_id
                 AND replacement.task_type = 'report'
            )
            UPDATE {qualified_schema}.reports AS report
            SET initial_task_run_id = request_runs.task_run_id
            FROM request_runs
            WHERE report.id = request_runs.report_id
              AND report.initial_task_run_id IS NULL
            """
        )
    )


def downgrade() -> None:
    schema = _relation_schema("reports")
    qualified_schema = _quote_identifier(schema)
    op.execute(
        sa.text(
            f"""
            UPDATE {qualified_schema}.ai_task_runs
            SET task_type = 'report'
            WHERE task_type = 'report_superseded'
            """
        )
    )
    op.drop_index(
        op.f("ix_reports_initial_task_run_id"),
        table_name="reports",
        schema=schema,
    )
    op.drop_constraint(
        "fk_reports_initial_task_run_id_ai_task_runs",
        "reports",
        type_="foreignkey",
        schema=schema,
    )
    op.drop_column("reports", "initial_task_run_id", schema=schema)
    op.drop_index(
        op.f("ix_ai_task_runs_superseded_by_task_run_id"),
        table_name="ai_task_runs",
        schema=schema,
    )
    op.drop_constraint(
        "fk_ai_task_runs_superseded_by_task_run_id",
        "ai_task_runs",
        type_="foreignkey",
        schema=schema,
    )
    op.drop_column("ai_task_runs", "superseded_by_task_run_id", schema=schema)


def _relation_schema(relation_name: str) -> str:
    schema = op.get_bind().scalar(
        sa.text(
            """
            SELECT namespace.nspname
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE relation.oid = to_regclass(:relation_name)
            """
        ),
        {"relation_name": relation_name},
    )
    if not schema:
        raise RuntimeError(f"Could not resolve schema for {relation_name}.")
    return str(schema)


def _quote_identifier(identifier: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote_identifier(identifier)
