from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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


def test_legacy_worker_guard_migration_fences_unleased_running_reports(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0052_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()

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
            migration_env.setenv("CELERY_VISIBILITY_TIMEOUT_SECONDS", "3600")
            migration_env.setenv("REPORT_LEGACY_WORKER_GRACE_SECONDS", "7200")
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0051_report_dispatch_claims")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, status, period_start, period_end,
                            filters_json, prompt_config_json,
                            generation_context_json, sections_config_json,
                            metrics_json, coverage_json
                        )
                        VALUES (
                            :id, 'Unfenced legacy report', 'running',
                            now() - interval '1 day', now(), '{}'::json,
                            '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json
                        )
                        """
                    ),
                    {"id": report_id},
                )

            before_upgrade = datetime.now(timezone.utc)
            command.upgrade(config, "0052_legacy_worker_guard")
            after_upgrade = datetime.now(timezone.utc)
            with schema_engine.connect() as connection:
                report_token, report_expiry = connection.execute(
                    text(
                        "SELECT generation_lease_token, generation_lease_expires_at "
                        "FROM reports WHERE id = :id"
                    ),
                    {"id": report_id},
                ).one()
                lease_fence, lease_token, lease_expiry = connection.execute(
                    text(
                        "SELECT generation_fence, lease_token, lease_expires_at "
                        "FROM report_generation_leases WHERE report_id = :id"
                    ),
                    {"id": report_id},
                ).one()

            assert report_token == f"legacy-unfenced:{report_id.hex}"
            assert before_upgrade + timedelta(seconds=7195) <= report_expiry
            assert report_expiry <= after_upgrade + timedelta(seconds=7205)
            assert lease_fence == 1
            assert lease_token == report_token
            assert lease_expiry == report_expiry

            command.downgrade(config, "0051_report_dispatch_claims")
            with schema_engine.connect() as connection:
                preserved_token = connection.scalar(
                    text("SELECT generation_lease_token FROM reports WHERE id = :id"),
                    {"id": report_id},
                )
            assert preserved_token == report_token
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
