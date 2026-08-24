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


def test_report_dispatch_protocol_migration_marks_old_binary_writes(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0054_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    existing_run_id = uuid.uuid4()
    old_binary_run_id = uuid.uuid4()

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
            command.upgrade(config, "0053_report_operation_receipts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, metadata_json
                        ) VALUES (
                            :id, 'report', 'manual', 'queued', '{}'::json
                        )
                        """
                    ),
                    {"id": existing_run_id},
                )

            command.upgrade(config, "0054_report_dispatch_protocol")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, metadata_json
                        ) VALUES (
                            :id, 'report', 'manual', 'queued', '{}'::json
                        )
                        """
                    ),
                    {"id": old_binary_run_id},
                )
                versions = dict(
                    connection.execute(
                        text(
                            "SELECT id, dispatch_protocol_version "
                            "FROM ai_task_runs WHERE id IN (:existing, :old_binary)"
                        ),
                        {
                            "existing": existing_run_id,
                            "old_binary": old_binary_run_id,
                        },
                    ).all()
                )

            assert versions == {
                existing_run_id: 1,
                old_binary_run_id: 1,
            }

            command.downgrade(config, "0053_report_operation_receipts")
            columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "ai_task_runs",
                    schema=schema_name,
                )
            }
            assert "dispatch_protocol_version" not in columns
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
