from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
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


def test_schedule_version_guard_migrates_in_active_schema(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0055_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)

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
            command.upgrade(config, "0054_report_dispatch_protocol")
            command.upgrade(config, "0055_schedule_version_guard")

            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_trigger AS trigger
                        JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = :schema
                          AND relation.relname = 'report_schedules'
                          AND trigger.tgname = 'trg_report_schedules_monotonic_version'
                          AND NOT trigger.tgisinternal
                        """
                    ),
                    {"schema": schema_name},
                ) == 1

            command.downgrade(config, "0054_report_dispatch_protocol")
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_proc AS function
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function.pronamespace
                        WHERE namespace.nspname = :schema
                          AND function.proname =
                              'threatlens_monotonic_report_schedule_version'
                        """
                    ),
                    {"schema": schema_name},
                ) == 0
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
