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


def test_system_operation_runs_migrate_and_round_trip(test_database_url, monkeypatch):
    schema_name = f"migration_0057_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    run_id = uuid.uuid4()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version '
                "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
            )
        )

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_database_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0056_report_task_lineage")
            command.upgrade(config, "0057_system_operations")

            inspector = inspect(schema_engine)
            columns = {
                column["name"]
                for column in inspector.get_columns(
                    "system_operation_runs",
                    schema=schema_name,
                )
            }
            indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes(
                    "system_operation_runs",
                    schema=schema_name,
                )
            }
            checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "system_operation_runs",
                    schema=schema_name,
                )
            }

            assert columns == {
                "id",
                "operation_type",
                "status",
                "initiated_by",
                "source",
                "metadata_json",
                "error_code",
                "error_message",
                "started_at",
                "finished_at",
                "created_at",
                "updated_at",
            }
            assert indexes["ix_system_operation_runs_started_at"] == ("started_at",)
            assert indexes["ix_system_operation_runs_type_started"] == (
                "operation_type",
                "started_at",
            )
            assert indexes["ix_system_operation_runs_status_started"] == (
                "status",
                "started_at",
            )
            assert checks >= {
                "ck_system_operation_runs_operation_type",
                "ck_system_operation_runs_status",
            }

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO system_operation_runs (
                            id, operation_type, status, initiated_by, source,
                            metadata_json, error_code, error_message
                        ) VALUES (
                            :id, 'backup', 'succeeded', 'pytest', 'offline_cli',
                            '{"archive_bytes": 1024}'::json, NULL, NULL
                        )
                        """
                    ),
                    {"id": run_id},
                )
            with schema_engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT operation_type, status, metadata_json "
                        "FROM system_operation_runs WHERE id = :id"
                    ),
                    {"id": run_id},
                ).one()
                assert row.operation_type == "backup"
                assert row.status == "succeeded"
                assert row.metadata_json == {"archive_bytes": 1024}

            command.downgrade(config, "0056_report_task_lineage")
            assert not inspect(schema_engine).has_table(
                "system_operation_runs",
                schema=schema_name,
            )

            command.upgrade(config, "0057_system_operations")
            assert inspect(schema_engine).has_table(
                "system_operation_runs",
                schema=schema_name,
            )
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
