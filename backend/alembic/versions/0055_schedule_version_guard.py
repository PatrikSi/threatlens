"""enforce monotonic report schedule versions

Revision ID: 0055_schedule_version_guard
Revises: 0054_report_dispatch_protocol
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0055_schedule_version_guard"
down_revision = "0054_report_dispatch_protocol"
branch_labels = None
depends_on = None

_FUNCTION_NAME = "threatlens_monotonic_report_schedule_version"
_TRIGGER_NAME = "trg_report_schedules_monotonic_version"


def upgrade() -> None:
    connection = op.get_bind()
    schema = _relation_schema("report_schedules")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
    op.execute(
        f"""
        CREATE FUNCTION {quoted_schema}.{_FUNCTION_NAME}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.updated_at IS NULL OR NEW.updated_at <= OLD.updated_at THEN
                NEW.updated_at := OLD.updated_at + interval '1 microsecond';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE UPDATE ON {quoted_schema}.report_schedules
        FOR EACH ROW
        EXECUTE FUNCTION {quoted_schema}.{_FUNCTION_NAME}()
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    schema = _relation_schema("report_schedules")
    quoted_schema = connection.dialect.identifier_preparer.quote_identifier(schema)
    op.execute(
        f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} "
        f"ON {quoted_schema}.report_schedules"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {quoted_schema}.{_FUNCTION_NAME}()"
    )


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
