from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_audit_snapshot_migration_backfills_and_guards_history(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0077_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    feed_id = uuid.uuid4()
    missing_feed_id = uuid.uuid4()
    live_audit_id = uuid.uuid4()
    missing_audit_id = uuid.uuid4()
    metadata_audit_id = uuid.uuid4()
    ungoverned_audit_id = uuid.uuid4()
    rolling_writer_audit_id = uuid.uuid4()

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
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0076_integration_metric_cohorts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Audit migration feed', :url, :digest, :label_id)"
                    ),
                    {
                        "id": feed_id,
                        "url": f"https://example.com/{feed_id}.xml",
                        "digest": uuid.uuid4().hex.ljust(64, "0"),
                        "label_id": QUARANTINE_HANDLING_LABEL_ID,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, resource_id, success, "
                        "metadata_json, authorization_elevation_ids) VALUES "
                        "(:live, 'feeds.update', 'feed', :feed_id, true, "
                        "'{\"name\": \"restricted\"}'::jsonb, '[]'::jsonb), "
                        "(:missing, 'feeds.delete', 'feed', :missing_feed_id, "
                        "true, '{}'::jsonb, '[]'::jsonb), "
                        "(:metadata, 'data_policy.access.not_served', "
                        "'custom_history', NULL, false, CAST(:metadata_json AS jsonb), "
                        "'[]'::jsonb), "
                        "(:ungoverned, 'iam.role.update', 'iam_role', :role_id, "
                        "true, '{}'::jsonb, '[]'::jsonb)"
                    ),
                    {
                        "live": live_audit_id,
                        "feed_id": str(feed_id),
                        "missing": missing_audit_id,
                        "missing_feed_id": str(missing_feed_id),
                        "metadata": metadata_audit_id,
                        "metadata_json": json.dumps(
                            {"handling_label_ids": [str(QUARANTINE_HANDLING_LABEL_ID)]}
                        ),
                        "ungoverned": ungoverned_audit_id,
                        "role_id": str(uuid.uuid4()),
                    },
                )

            command.upgrade(config, "0077_audit_policy_snapshots")
            columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "audit_logs", schema=schema_name
                )
            }
            assert {"data_access_governed", "data_access_label_ids"} <= columns

            with schema_engine.begin() as connection:
                rows = {
                    row.id: (row.data_access_governed, row.data_access_label_ids)
                    for row in connection.execute(
                        text(
                            "SELECT id, data_access_governed, "
                            "data_access_label_ids FROM audit_logs "
                            "WHERE id IN (:live, :missing, :metadata, :ungoverned)"
                        ),
                        {
                            "live": live_audit_id,
                            "missing": missing_audit_id,
                            "metadata": metadata_audit_id,
                            "ungoverned": ungoverned_audit_id,
                        },
                    ).all()
                }
                assert rows[live_audit_id] == (
                    True,
                    [str(QUARANTINE_HANDLING_LABEL_ID)],
                )
                assert rows[missing_audit_id] == (True, [])
                assert rows[metadata_audit_id] == (
                    True,
                    [str(QUARANTINE_HANDLING_LABEL_ID)],
                )
                assert rows[ungoverned_audit_id] == (False, [])
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, resource_id, success, "
                        "metadata_json, authorization_elevation_ids) VALUES "
                        "(:id, 'feeds.refresh', 'feed', :feed_id, true, "
                        "'{}'::jsonb, '[]'::jsonb)"
                    ),
                    {
                        "id": rolling_writer_audit_id,
                        "feed_id": str(feed_id),
                    },
                )
                assert connection.execute(
                    text(
                        "SELECT data_access_governed, data_access_label_ids "
                        "FROM audit_logs WHERE id = :id"
                    ),
                    {"id": rolling_writer_audit_id},
                ).one() == (True, [str(QUARANTINE_HANDLING_LABEL_ID)])

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE audit_logs SET data_access_label_ids = "
                            "jsonb_build_array(CAST(:label_id AS text)) "
                            "WHERE id = :id"
                        ),
                        {
                            "id": ungoverned_audit_id,
                            "label_id": str(QUARANTINE_HANDLING_LABEL_ID),
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE audit_logs SET data_access_label_ids = "
                            "'[]'::jsonb WHERE id = :id"
                        ),
                        {"id": live_audit_id},
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0076_integration_metric_cohorts")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )
            command.downgrade(config, "0076_integration_metric_cohorts")
            columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "audit_logs", schema=schema_name
                )
            }
            assert "data_access_governed" not in columns
            assert "data_access_label_ids" not in columns
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
