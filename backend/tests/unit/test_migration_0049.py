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


def test_report_generation_fence_migration_seeds_legacy_lease(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0049_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()
    legacy_token = uuid.uuid4().hex

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
            command.upgrade(config, "0048_feed_fetch_fence")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, generation_context_json,
                            sections_config_json, metrics_json, coverage_json,
                            generation_lease_token, generation_lease_expires_at
                        )
                        VALUES (
                            :id, 'Active legacy report', now() - interval '1 day', now(),
                            '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json, :token, now() + interval '5 minutes'
                        )
                        """
                    ),
                    {"id": report_id, "token": legacy_token},
                )

            command.upgrade(config, "0049_report_generation_fence")
            with schema_engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                seeded = connection.execute(
                    text(
                        "SELECT generation_fence, lease_token, lease_expires_at "
                        "FROM report_generation_leases WHERE report_id = :id"
                    ),
                    {"id": report_id},
                ).one()
            assert revision == "0049_report_generation_fence"
            assert seeded.generation_fence == 1
            assert seeded.lease_token == legacy_token
            assert seeded.lease_expires_at is not None

            command.downgrade(config, "0048_feed_fetch_fence")
            assert "report_generation_leases" not in inspect(
                schema_engine
            ).get_table_names(schema=schema_name)

            command.upgrade(config, "0049_report_generation_fence")
            assert "report_generation_leases" in inspect(schema_engine).get_table_names(
                schema=schema_name
            )
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
