from __future__ import annotations

import json
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


def test_report_task_lineage_survives_upgrade_and_rollback(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0056_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    report_id = uuid.uuid4()
    initial_id = uuid.uuid4()
    superseded_retry_id = uuid.uuid4()
    replacement_id = uuid.uuid4()
    origin_missing_report_id = uuid.uuid4()
    origin_missing_retry_id = uuid.uuid4()

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
            command.upgrade(config, "0055_schedule_version_guard")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, generation_context_json,
                            sections_config_json, metrics_json, coverage_json,
                            created_at, updated_at
                        ) VALUES (
                            :report_id, 'Migration report', now() - interval '1 day',
                            now(), '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json,
                            '2026-08-24T09:00:00Z', '2026-08-24T09:00:00Z'
                        )
                        """
                    ),
                    {"report_id": report_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, generation_context_json,
                            sections_config_json, metrics_json, coverage_json,
                            created_at, updated_at
                        ) VALUES (
                            :report_id, 'Origin missing', now() - interval '1 day',
                            now(), '{}'::json, '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json,
                            '2026-08-24T09:00:00Z', '2026-08-24T09:00:00Z'
                        )
                        """
                    ),
                    {"report_id": origin_missing_report_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, report_id,
                            dispatch_protocol_version, metadata_json, created_at,
                            updated_at
                        ) VALUES (
                            :replacement_id, 'report', 'manual', 'queued',
                            :report_id, 2, '{}'::json,
                            '2026-08-24T09:59:59Z', '2026-08-24T10:00:01Z'
                        )
                        """
                    ),
                    {"replacement_id": replacement_id, "report_id": report_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, report_id,
                            dispatch_protocol_version, metadata_json, finished_at,
                            created_at, updated_at
                        ) VALUES (
                            :initial_id, 'report', 'manual', 'error',
                            :report_id, 2,
                            '{"report_request_origin": true}'::json,
                            '2026-08-24T11:00:01Z',
                            '2026-08-24T11:00:00Z', '2026-08-24T11:00:01Z'
                        )
                        """
                    ),
                    {"initial_id": initial_id, "report_id": report_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, reason, report_id,
                            dispatch_protocol_version, metadata_json, finished_at,
                            created_at, updated_at
                        ) VALUES (
                            :superseded_retry_id, 'report_superseded', 'manual', 'skipped',
                            'superseded_for_fenced_dispatch', :report_id, 1,
                            CAST(:metadata AS json), '2026-08-24T10:00:01Z',
                            '2026-08-24T10:00:00Z', '2026-08-24T10:00:01Z'
                        )
                        """
                    ),
                    {
                        "superseded_retry_id": superseded_retry_id,
                        "report_id": report_id,
                        "metadata": json.dumps(
                            {
                                "report_request_origin": False,
                                "superseded_by_task_run_id": str(replacement_id),
                            }
                        ),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_runs (
                            id, task_type, trigger_source, status, report_id,
                            dispatch_protocol_version, metadata_json, finished_at,
                            created_at, updated_at
                        ) VALUES (
                            :retry_id, 'report', 'manual', 'error', :report_id,
                            2, '{"report_request_origin": false}'::json,
                            '2026-08-24T08:59:01Z',
                            '2026-08-24T08:59:00Z', '2026-08-24T08:59:01Z'
                        )
                        """
                    ),
                    {
                        "retry_id": origin_missing_retry_id,
                        "report_id": origin_missing_report_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO ai_task_events (
                            id, task_run_id, event_type, payload_json, created_at
                        ) VALUES
                            (:initial_event_id, :initial_id, 'queued', '{}'::json,
                                '2026-08-24T09:00:00Z'),
                            (:retry_event_id, :superseded_retry_id, 'queued', '{}'::json,
                                '2026-08-24T08:59:00Z'),
                            (:replacement_event_id, :replacement_id, 'queued', '{}'::json,
                                '2026-08-24T09:00:00Z'),
                            (:missing_event_id, :missing_retry_id, 'queued', '{}'::json,
                                '2026-08-24T09:00:00Z')
                        """
                    ),
                    {
                        "initial_event_id": uuid.uuid4(),
                        "initial_id": initial_id,
                        "retry_event_id": uuid.uuid4(),
                        "superseded_retry_id": superseded_retry_id,
                        "replacement_event_id": uuid.uuid4(),
                        "replacement_id": replacement_id,
                        "missing_event_id": uuid.uuid4(),
                        "missing_retry_id": origin_missing_retry_id,
                    },
                )

            command.upgrade(config, "0056_report_task_lineage")
            with schema_engine.connect() as connection:
                lineage = connection.execute(
                    text(
                        """
                        SELECT task_type, superseded_by_task_run_id
                        FROM ai_task_runs WHERE id = :superseded_retry_id
                        """
                    ),
                    {"superseded_retry_id": superseded_retry_id},
                ).one()
                assert lineage == ("report", replacement_id)
                assert (
                    connection.scalar(
                        text("SELECT initial_task_run_id FROM reports WHERE id = :id"),
                        {"id": report_id},
                    )
                    == initial_id
                )
                assert connection.scalar(
                    text("SELECT initial_task_run_id FROM reports WHERE id = :id"),
                    {"id": origin_missing_report_id},
                ) is None
                assert (
                    connection.scalar(
                        text(
                            """
                        SELECT count(*) FROM ai_task_runs
                        WHERE report_id = :report_id AND task_type != 'report'
                        """
                        ),
                        {"report_id": report_id},
                    )
                    == 0
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE reports SET initial_task_run_id = :wrong_id "
                        "WHERE id = :report_id"
                    ),
                    {"wrong_id": replacement_id, "report_id": report_id},
                )
                connection.execute(
                    text(
                        "UPDATE ai_task_runs SET task_type = 'report_superseded' "
                        "WHERE id = :id"
                    ),
                    {"id": superseded_retry_id},
                )

            command.upgrade(config, "0057_report_task_lineage_compat")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT initial_task_run_id FROM reports WHERE id = :id"),
                        {"id": report_id},
                    )
                    == initial_id
                )
                assert (
                    connection.scalar(
                        text("SELECT task_type FROM ai_task_runs WHERE id = :id"),
                        {"id": superseded_retry_id},
                    )
                    == "report"
                )
                assert connection.scalar(
                    text("SELECT initial_task_run_id FROM reports WHERE id = :id"),
                    {"id": origin_missing_report_id},
                ) is None

            command.downgrade(config, "0055_schedule_version_guard")
            columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "ai_task_runs",
                    schema=schema_name,
                )
            }
            assert "superseded_by_task_run_id" not in columns
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT task_type FROM ai_task_runs WHERE id = :id"),
                        {"id": superseded_retry_id},
                    )
                    == "report"
                )

            command.upgrade(config, "0057_report_task_lineage_compat")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            """
                        SELECT superseded_by_task_run_id
                        FROM ai_task_runs WHERE id = :id
                        """
                        ),
                        {"id": superseded_retry_id},
                    )
                    == replacement_id
                )
                assert (
                    connection.scalar(
                        text("SELECT initial_task_run_id FROM reports WHERE id = :id"),
                        {"id": report_id},
                    )
                    == initial_id
                )
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
