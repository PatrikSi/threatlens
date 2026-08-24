from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    return url.render_as_string(hide_password=False)


def test_report_idempotency_compat_repairs_rename_based_draft(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0050_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()
    key_hash = "a" * 64

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version '
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv(
                "DATABASE_URL",
                schema_database_url.replace("%", "%%"),
            )
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0049_report_generation_fence")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, generation_context_json,
                            sections_config_json, metrics_json, coverage_json,
                            request_idempotency_key_hash
                        )
                        VALUES (
                            :id, 'Compatibility report', now() - interval '1 day', now(),
                            '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json, :key_hash
                        )
                        """
                    ),
                    {"id": report_id, "key_hash": key_hash},
                )
                connection.execute(
                    text(
                        "ALTER TABLE reports DROP CONSTRAINT "
                        "uq_reports_owner_request_idempotency_key_hash"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE reports DROP CONSTRAINT "
                        "uq_reports_owner_request_idempotency_key"
                    )
                )
                connection.execute(
                    text("ALTER TABLE reports DROP COLUMN request_idempotency_key")
                )
                connection.execute(
                    text(
                        "ALTER TABLE reports ADD CONSTRAINT "
                        "uq_reports_owner_request_idempotency_key UNIQUE "
                        "(owner_user_id, request_idempotency_key_hash)"
                    )
                )

            command.upgrade(config, "0050_report_idempotency_compat")
            inspector = inspect(schema_engine)
            columns = {
                column["name"]
                for column in inspector.get_columns("reports", schema=schema_name)
            }
            constraints = {
                constraint["name"]: tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(
                    "reports", schema=schema_name
                )
            }
            assert "request_idempotency_key" in columns
            assert "request_idempotency_key_hash" in columns
            assert constraints["uq_reports_owner_request_idempotency_key"] == (
                "owner_user_id",
                "request_idempotency_key",
            )
            assert constraints["uq_reports_owner_request_idempotency_key_hash"] == (
                "owner_user_id",
                "request_idempotency_key_hash",
            )
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT request_idempotency_key_hash FROM reports "
                            "WHERE id = :id"
                        ),
                        {"id": report_id},
                    )
                    == key_hash
                )

            command.downgrade(config, "0049_report_generation_fence")
            downgraded_columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "reports", schema=schema_name
                )
            }
            assert {
                "request_idempotency_key",
                "request_idempotency_key_hash",
            } <= downgraded_columns
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
