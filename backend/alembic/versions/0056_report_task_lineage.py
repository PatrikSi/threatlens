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
            UPDATE {qualified_schema}.ai_task_runs AS legacy
            SET superseded_by_task_run_id = replacement.id
            FROM {qualified_schema}.ai_task_runs AS replacement
            WHERE legacy.superseded_by_task_run_id IS NULL
              AND legacy.metadata_json ->> 'superseded_by_task_run_id' = replacement.id::text
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH initial_runs AS (
                SELECT DISTINCT ON (report_id) report_id, id
                FROM {qualified_schema}.ai_task_runs
                WHERE report_id IS NOT NULL
                  AND task_type IN ('report', 'report_superseded')
                ORDER BY report_id,
                    (reason = 'superseded_for_fenced_dispatch'
                        AND metadata_json ->> 'superseded_by_task_run_id'
                            IS NOT NULL) DESC,
                    created_at ASC, id ASC
            )
            UPDATE {qualified_schema}.reports AS report
            SET initial_task_run_id = initial_runs.id
            FROM initial_runs
            WHERE report.id = initial_runs.report_id
              AND report.initial_task_run_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {qualified_schema}.ai_task_runs
            SET task_type = 'report_superseded'
            WHERE superseded_by_task_run_id IS NOT NULL
              AND task_type = 'report'
              AND reason = 'superseded_for_fenced_dispatch'
            """
        )
    )


def downgrade() -> None:
    schema = _relation_schema("reports")
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
    # Keep the terminal task_type marker. Older binaries ignore that historical
    # row and continue resolving the still-linked replacement after rollback.


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
