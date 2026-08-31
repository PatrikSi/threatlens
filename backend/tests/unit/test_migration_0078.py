from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)


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


def test_ai_telemetry_migration_repairs_and_taints_retained_history(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0078_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    restricted_label_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    item_run_id = uuid.uuid4()
    unresolved_run_id = uuid.uuid4()
    system_run_id = uuid.uuid4()
    item_usage_id = uuid.uuid4()
    unresolved_usage_id = uuid.uuid4()
    system_usage_id = uuid.uuid4()
    linked_audit_id = uuid.uuid4()
    malformed_audit_id = uuid.uuid4()
    rolling_audit_id = uuid.uuid4()
    mixed_audit_id = uuid.uuid4()
    unresolved_ai_audit_id = uuid.uuid4()
    settings_audit_id = uuid.uuid4()
    rolling_legacy_run_id = uuid.uuid4()
    rolling_legacy_usage_id = uuid.uuid4()
    fenced_audit_id = uuid.uuid4()

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
            command.upgrade(config, "0077_audit_policy_snapshots")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO handling_labels "
                        "(id, key, name, color) VALUES "
                        "(:id, 'migration-0078-restricted', "
                        "'Migration 0078 restricted', '#991B1B')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Migration 0078 feed', 'encrypted', :digest, :label)"
                    ),
                    {
                        "id": feed_id,
                        "digest": "8" * 64,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash) "
                        "VALUES (:id, :feed, 'https://example.com/item', "
                        "'Migration item', :dedupe, :hash)"
                    ),
                    {
                        "id": item_id,
                        "feed": feed_id,
                        "dedupe": f"migration-0078-{item_id}",
                        "hash": "9" * 64,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_task_runs "
                        "(id, task_type, trigger_source, status, item_id, "
                        "metadata_json) VALUES "
                        "(:item_run, 'item_enrichment', 'manual', 'ready', "
                        ":item, '{}'::json), "
                        "(:unresolved, 'reprocess', 'manual', 'ready', NULL, "
                        '\'{"scope": "legacy"}\'::json), '
                        "(:system, 'connection_test', 'manual', 'ready', NULL, "
                        "'{}'::json)"
                    ),
                    {
                        "item_run": item_run_id,
                        "item": item_id,
                        "unresolved": unresolved_run_id,
                        "system": system_run_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_events "
                        "(id, feature_type, success, item_id) VALUES "
                        "(:item_usage, 'item_enrichment', true, :item), "
                        "(:unresolved, 'legacy_unknown', false, NULL), "
                        "(:system, 'connection_test', true, NULL)"
                    ),
                    {
                        "item_usage": item_usage_id,
                        "item": item_id,
                        "unresolved": unresolved_usage_id,
                        "system": system_usage_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, resource_id, success, "
                        "metadata_json, authorization_elevation_ids) VALUES "
                        "(:linked, 'ai.reprocess.queue', 'ai_task_run', "
                        ":run_id, true, '{}'::jsonb, '[]'::jsonb)"
                    ),
                    {"linked": linked_audit_id, "run_id": str(item_run_id)},
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, success, metadata_json, "
                        "authorization_elevation_ids, data_access_governed, "
                        "data_access_label_ids) VALUES "
                        "(:id, 'custom.governed', 'custom_resource', true, "
                        "'{}'::jsonb, '[]'::jsonb, true, "
                        "CAST(:labels AS jsonb))"
                    ),
                    {
                        "id": malformed_audit_id,
                        "labels": json.dumps(["not-a-uuid"]),
                    },
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0078_ai_telemetry_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )
            command.upgrade(config, "0078_ai_telemetry_policy")
            inspector = inspect(schema_engine)
            assert {
                "data_access_scope",
                "data_access_lineage_complete",
            } <= {
                column["name"]
                for column in inspector.get_columns("ai_task_runs", schema=schema_name)
            }
            assert {
                "data_access_scope",
                "task_run_id_snapshot",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "ai_usage_events", schema=schema_name
                )
            }
            assert inspector.has_table(
                "audit_log_data_access_labels", schema=schema_name
            )
            assert inspector.has_table(
                "audit_log_data_access_feeds", schema=schema_name
            )

            insert_started = Event()

            def insert_old_writer_audit() -> None:
                with schema_engine.begin() as writer:
                    insert_started.set()
                    writer.execute(
                        text(
                            "INSERT INTO audit_logs "
                            "(id, action, resource_type, success, "
                            "metadata_json, authorization_elevation_ids) "
                            "VALUES (:id, 'ai.reprocess.queue', "
                            "'ai_task_run', true, '{}'::jsonb, '[]'::jsonb)"
                        ),
                        {"id": fenced_audit_id},
                    )

            with schema_engine.connect() as policy_connection:
                policy_transaction = policy_connection.begin()
                policy_connection.execute(
                    text("SELECT revision FROM data_policy_state WHERE id = 1 FOR UPDATE")
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    insert_future = executor.submit(insert_old_writer_audit)
                    assert insert_started.wait(timeout=2)
                    with pytest.raises(FutureTimeoutError):
                        insert_future.result(timeout=0.2)
                    policy_transaction.commit()
                    insert_future.result(timeout=5)

            with schema_engine.connect() as connection:
                assert _audit_labels(connection, fenced_audit_id) == {
                    QUARANTINE_HANDLING_LABEL_ID
                }

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai_task_runs "
                        "(id, task_type, trigger_source, status, item_id, "
                        "metadata_json) VALUES "
                        "(:id, 'item_enrichment', 'automatic', 'queued', "
                        ":item, '{}'::json)"
                    ),
                    {"id": rolling_legacy_run_id, "item": item_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_usage_events "
                        "(id, feature_type, success, item_id) VALUES "
                        "(:id, 'item_enrichment', false, :item)"
                    ),
                    {"id": rolling_legacy_usage_id, "item": item_id},
                )
                run_scopes = {
                    row.id: (row.data_access_scope, row.data_access_lineage_complete)
                    for row in connection.execute(
                        text(
                            "SELECT id, data_access_scope, "
                            "data_access_lineage_complete FROM ai_task_runs"
                        )
                    )
                }
                assert run_scopes[item_run_id] == ("governed", True)
                assert run_scopes[unresolved_run_id] == ("governed", True)
                assert run_scopes[system_run_id] == ("system", True)
                assert run_scopes[rolling_legacy_run_id] == ("governed", False)
                usage_scopes = dict(
                    connection.execute(
                        text("SELECT id, data_access_scope FROM ai_usage_events")
                    ).all()
                )
                assert usage_scopes[item_usage_id] == "governed"
                assert usage_scopes[unresolved_usage_id] == "governed"
                assert usage_scopes[system_usage_id] == "system"
                assert usage_scopes[rolling_legacy_usage_id] == "governed"
                assert _resource_labels(
                    connection, "ai_task_run", rolling_legacy_run_id
                ) == set()
                assert _resource_labels(
                    connection, "ai_usage_event", rolling_legacy_usage_id
                ) == set()
                assert _resource_labels(connection, "ai_task_run", item_run_id) == {
                    restricted_label_id
                }
                assert _resource_labels(
                    connection, "ai_task_run", unresolved_run_id
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert _resource_labels(
                    connection, "ai_usage_event", unresolved_usage_id
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert _audit_labels(connection, linked_audit_id) == {
                    restricted_label_id
                }
                assert connection.scalar(
                    text(
                        "SELECT data_access_governed FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": linked_audit_id},
                ) is True
                assert _audit_labels(connection, malformed_audit_id) == {
                    QUARANTINE_HANDLING_LABEL_ID
                }
                assert set(
                    connection.scalars(
                        text(
                            "SELECT source_feed_id_snapshot FROM "
                            "audit_log_data_access_feeds "
                            "WHERE audit_log_id = :id"
                        ),
                        {"id": linked_audit_id},
                    )
                ) == {feed_id}

                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, resource_id, success, "
                        "metadata_json, authorization_elevation_ids) VALUES "
                        "(:id, 'ai.run.cancel', 'ai_task_run', :run_id, true, "
                        "'{}'::jsonb, '[]'::jsonb)"
                    ),
                    {"id": rolling_audit_id, "run_id": str(item_run_id)},
                )
                assert _audit_labels(connection, rolling_audit_id) == {
                    restricted_label_id
                }
                assert connection.scalar(
                    text(
                        "SELECT data_access_governed FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": rolling_audit_id},
                ) is True
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, success, metadata_json, "
                        "authorization_elevation_ids, data_access_governed, "
                        "data_access_label_ids) VALUES "
                        "(:id, 'custom.governed', 'custom_resource', true, "
                        "'{}'::jsonb, '[]'::jsonb, true, "
                        "CAST(:labels AS jsonb))"
                    ),
                    {
                        "id": mixed_audit_id,
                        "labels": json.dumps(
                            [str(restricted_label_id), "not-a-uuid"]
                        ),
                    },
                )
                assert _audit_labels(connection, mixed_audit_id) == {
                    restricted_label_id,
                    QUARANTINE_HANDLING_LABEL_ID,
                }
                with pytest.raises(IntegrityError, match="snapshots are immutable"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE audit_logs SET "
                                "data_access_governed = false, "
                                "data_access_label_ids = '[]'::jsonb "
                                "WHERE id = :id"
                            ),
                            {"id": mixed_audit_id},
                        )
                assert connection.scalar(
                    text(
                        "SELECT data_access_governed FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": mixed_audit_id},
                ) is True
                assert _audit_labels(connection, mixed_audit_id) == {
                    restricted_label_id,
                    QUARANTINE_HANDLING_LABEL_ID,
                }
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, success, metadata_json, "
                        "authorization_elevation_ids) VALUES "
                        "(:id, 'ai.reprocess.queue', 'ai_task_run', true, "
                        "'{}'::jsonb, '[]'::jsonb)"
                    ),
                    {"id": unresolved_ai_audit_id},
                )
                assert _audit_labels(connection, unresolved_ai_audit_id) == {
                    QUARANTINE_HANDLING_LABEL_ID
                }
                assert connection.scalar(
                    text(
                        "SELECT data_access_governed FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": unresolved_ai_audit_id},
                ) is True
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, success, metadata_json, "
                        "authorization_elevation_ids) VALUES "
                        "(:id, 'ai.settings.update', 'ai_settings', true, "
                        "'{}'::jsonb, '[]'::jsonb)"
                    ),
                    {"id": settings_audit_id},
                )
                assert _audit_labels(connection, settings_audit_id) == set()
                assert connection.scalar(
                    text(
                        "SELECT data_access_governed FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": settings_audit_id},
                ) is False
                connection.execute(
                    text(
                        "UPDATE feeds SET handling_label_id = :label WHERE id = :feed"
                    ),
                    {"label": UNRESTRICTED_HANDLING_LABEL_ID, "feed": feed_id},
                )
                assert _audit_labels(connection, linked_audit_id) == {
                    restricted_label_id,
                    UNRESTRICTED_HANDLING_LABEL_ID,
                }
                assert _audit_labels(connection, rolling_audit_id) == {
                    restricted_label_id,
                    UNRESTRICTED_HANDLING_LABEL_ID,
                }

                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0077_audit_policy_snapshots")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )
            command.downgrade(config, "0077_audit_policy_snapshots")
            downgraded = inspect(schema_engine)
            assert not downgraded.has_table(
                "audit_log_data_access_labels", schema=schema_name
            )
            assert "data_access_scope" not in {
                column["name"]
                for column in downgraded.get_columns("ai_task_runs", schema=schema_name)
            }
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM data_access_envelopes "
                            "WHERE resource_type IN "
                            "('ai_task_run', 'ai_usage_event')"
                        )
                    )
                    == 0
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _resource_labels(connection, resource_type: str, resource_id: uuid.UUID):
    return set(
        connection.scalars(
            text(
                "SELECT label.label_id FROM data_access_envelopes AS envelope "
                "JOIN data_access_envelope_labels AS label "
                "ON label.envelope_id = envelope.id "
                "WHERE envelope.resource_type = :type "
                "AND envelope.resource_id = :id"
            ),
            {"type": resource_type, "id": resource_id},
        )
    )


def _audit_labels(connection, audit_log_id: uuid.UUID):
    return set(
        connection.scalars(
            text(
                "SELECT label_id FROM audit_log_data_access_labels "
                "WHERE audit_log_id = :id"
            ),
            {"id": audit_log_id},
        )
    )
