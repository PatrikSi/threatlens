from __future__ import annotations

import uuid
from datetime import timedelta
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


def test_report_dispatch_claim_migration_backfills_published_work(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0051_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()
    run_id = uuid.uuid4()

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
                            sections_config_json, metrics_json, coverage_json
                        )
                        VALUES (
                            :id, 'Published report', now() - interval '1 day', now(),
                            '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json
                        )
                        """
                    ),
                    {"id": report_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, report_id,
                            celery_task_id, metadata_json
                        )
                        VALUES (
                            :id, 'report', 'manual', 'queued', :report_id,
                            :task_id, '{}'::json
                        )
                        """
                    ),
                    {
                        "id": run_id,
                        "report_id": report_id,
                        "task_id": f"report-{run_id}",
                    },
                )

            command.upgrade(config, "0051_report_dispatch_claims")
            with schema_engine.connect() as connection:
                published_at, next_attempt_at = connection.execute(
                    text(
                        "SELECT dispatch_published_at, dispatch_next_attempt_at "
                        "FROM ai_task_runs WHERE id = :id"
                    ),
                    {"id": run_id},
                ).one()
            assert published_at is not None
            assert next_attempt_at is not None
            assert next_attempt_at >= published_at + timedelta(minutes=4)

            command.downgrade(config, "0049_report_generation_fence")
            columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "ai_task_runs", schema=schema_name
                )
            }
            assert "dispatch_claim_token" not in columns
            assert "dispatch_claim_expires_at" not in columns
            assert "dispatch_published_at" not in columns
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
