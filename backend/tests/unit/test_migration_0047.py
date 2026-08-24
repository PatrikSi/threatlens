from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

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


def test_report_dispatch_migration_hashes_keys_and_repairs_active_runs(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0047_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()
    older_run_id = uuid.uuid4()
    newer_run_id = uuid.uuid4()
    raw_key = "legacy-report-key-" + "x" * 180
    expected_hash = hashlib.sha256(
        f"report:create\0{raw_key}".encode("utf-8")
    ).hexdigest()

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
            command.upgrade(config, "0046_report_resilience")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, generation_context_json,
                            sections_config_json, metrics_json, coverage_json,
                            request_idempotency_key
                        )
                        VALUES (
                            :id, 'Migration report', now() - interval '7 days', now(),
                            '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json, :raw_key
                        )
                        """
                    ),
                    {"id": report_id, "raw_key": raw_key},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, report_id,
                            metadata_json, created_at, updated_at
                        )
                        VALUES
                            (
                                :older_id, 'report', 'manual', 'running', :report_id,
                                '{}'::json, now() - interval '1 minute', now()
                            ),
                            (
                                :newer_id, 'report', 'manual', 'queued', :report_id,
                                '{}'::json, now(), now()
                            )
                        """
                    ),
                    {
                        "older_id": older_run_id,
                        "newer_id": newer_run_id,
                        "report_id": report_id,
                    },
                )

            command.upgrade(config, "head")
            with schema_engine.connect() as connection:
                stored_hash = connection.scalar(
                    text(
                        "SELECT request_idempotency_key_hash FROM reports "
                        "WHERE id = :report_id"
                    ),
                    {"report_id": report_id},
                )
                runs = connection.execute(
                    text(
                        "SELECT id, status, reason, finished_at FROM ai_task_runs "
                        "WHERE report_id = :report_id ORDER BY created_at"
                    ),
                    {"report_id": report_id},
                ).all()

            assert stored_hash == expected_hash
            assert runs[0][0] == older_run_id
            assert runs[0][1:3] == ("error", "superseded_duplicate")
            assert runs[0][3] is not None
            assert runs[1][0] == newer_run_id
            assert runs[1][1] == "queued"

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            """
                            INSERT INTO ai_task_runs (
                                id, task_type, trigger_source, status,
                                report_id, metadata_json
                            )
                            VALUES (
                                :id, 'report', 'manual', 'queued',
                                :report_id, '{}'::json
                            )
                            """
                        ),
                        {"id": uuid.uuid4(), "report_id": report_id},
                    )

            command.downgrade(config, "0046_report_resilience")
            with schema_engine.connect() as connection:
                downgraded_value = connection.scalar(
                    text(
                        "SELECT request_idempotency_key FROM reports "
                        "WHERE id = :report_id"
                    ),
                    {"report_id": report_id},
                )
            assert downgraded_value == expected_hash

            command.upgrade(config, "head")
            with schema_engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                reupgraded_hash = connection.scalar(
                    text(
                        "SELECT request_idempotency_key_hash FROM reports "
                        "WHERE id = :report_id"
                    ),
                    {"report_id": report_id},
                )
            assert revision == "0047_report_dispatch"
            assert reupgraded_hash == expected_hash
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()
