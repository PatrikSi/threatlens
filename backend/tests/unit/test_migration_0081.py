from __future__ import annotations

import uuid
from pathlib import Path

import pytest
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
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_activation_migration_is_self_contained():
    source = (
        _BACKEND_DIR / "alembic/versions/0081_data_policy_activation.py"
    ).read_text(encoding="utf-8")

    assert "from app." not in source
    assert "\nimport app." not in source


def test_activation_migration_guards_integrity_and_coverage_transitions(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0081_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
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
            command.upgrade(config, "0080_action_approval_policy")

            command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 1
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="while audit or enforcement"):
                command.downgrade(config, "0080_action_approval_policy")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )
            with pytest.raises(RuntimeError, match="expected 1"):
                command.downgrade(config, "0080_action_approval_policy")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET coverage_version = 1 WHERE id = 1"
                    )
                )
            command.downgrade(config, "0080_action_approval_policy")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 0
                )

            envelope_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'unsupported_resource', :resource_id, 0, 1)"
                    ),
                    {"id": envelope_id, "resource_id": uuid.uuid4()},
                )
            with pytest.raises(RuntimeError, match="database integrity checks failed"):
                command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 0
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM data_access_envelopes WHERE id = :id"),
                    {"id": envelope_id},
                )

            approval_id = uuid.uuid4()
            approval_envelope_id = uuid.uuid4()
            approval_source_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'action_approval', :resource_id, 1, 1)"
                    ),
                    {"id": approval_envelope_id, "resource_id": approval_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_sources "
                        "(id, envelope_id, source_type, source_id, "
                        "source_version, handling_label_id, "
                        "captured_policy_revision) VALUES "
                        "(:id, :envelope_id, 'borrowed', 'other-resource', "
                        "'v1', :label_id, 1)"
                    ),
                    {
                        "id": approval_source_id,
                        "envelope_id": approval_envelope_id,
                        "label_id": "00000000-0000-4000-8000-000000000201",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "VALUES (:envelope_id, :label_id, 1)"
                    ),
                    {
                        "envelope_id": approval_envelope_id,
                        "label_id": "00000000-0000-4000-8000-000000000201",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO action_approval_requests "
                        "(id, action_type, action_label_snapshot, "
                        "audit_action_snapshot, requester_permission_snapshot, "
                        "approver_permission_snapshot, action_definition_version, "
                        "target_data_policy_version, data_access_scope, "
                        "data_access_lineage_complete, data_access_source_type, "
                        "target_type, target_id, target_revision, target_snapshot, "
                        "payload_json, payload_digest, requested_by_email_snapshot, "
                        "request_reason, expires_at, status, revision) VALUES "
                        "(:id, 'unregistered.borrowed', 'Borrowed lineage', "
                        "'unregistered.borrowed', 'read:iam', 'write:iam', 1, 1, "
                        "'governed', true, 'unresolved', 'unknown', 'target', 1, "
                        "'{}'::jsonb, '{}'::jsonb, :digest, 'admin@example.com', "
                        "'Validate borrowed lineage', now() + interval '1 hour', "
                        "'pending', 1)"
                    ),
                    {"id": approval_id, "digest": "a" * 64},
                )
            with pytest.raises(RuntimeError, match="action_approval_data_policy"):
                command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM action_approval_requests WHERE id = :id"),
                    {"id": approval_id},
                )
                connection.execute(
                    text("DELETE FROM data_access_envelopes WHERE id = :id"),
                    {"id": approval_envelope_id},
                )

            audit_resource_id = uuid.uuid4()
            audit_envelope_id = uuid.uuid4()
            audit_source_id = uuid.uuid4()
            audit_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'report', :resource_id, 1, 1)"
                    ),
                    {
                        "id": audit_envelope_id,
                        "resource_id": audit_resource_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_sources "
                        "(id, envelope_id, source_type, source_id, "
                        "source_version, handling_label_id, "
                        "captured_policy_revision) VALUES "
                        "(:id, :envelope_id, 'item', 'audit-source', 'v1', "
                        ":label_id, 1)"
                    ),
                    {
                        "id": audit_source_id,
                        "envelope_id": audit_envelope_id,
                        "label_id": "00000000-0000-4000-8000-000000000201",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "VALUES (:envelope_id, :label_id, 1)"
                    ),
                    {
                        "envelope_id": audit_envelope_id,
                        "label_id": "00000000-0000-4000-8000-000000000201",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, action, resource_type, resource_id, success, "
                        "metadata_json, authorization_elevation_ids, "
                        "data_access_governed, data_access_label_ids) VALUES "
                        "(:id, 'reports.generate.complete', 'report', "
                        ":resource_id, true, '{}'::jsonb, '[]'::jsonb, true, "
                        "jsonb_build_array(CAST(:quarantine AS text)))"
                    ),
                    {
                        "id": audit_id,
                        "resource_id": str(audit_resource_id),
                        "quarantine": "00000000-0000-4000-8000-000000000202",
                    },
                )
                connection.execute(
                    text(
                        "DELETE FROM audit_log_data_access_labels "
                        "WHERE audit_log_id = :audit_id AND label_id = :label_id"
                    ),
                    {
                        "audit_id": audit_id,
                        "label_id": "00000000-0000-4000-8000-000000000201",
                    },
                )
            with pytest.raises(RuntimeError, match="normalized_audit_lineage"):
                command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"),
                    {"id": audit_id},
                )
                connection.execute(
                    text("DELETE FROM data_access_envelopes WHERE id = :id"),
                    {"id": audit_envelope_id},
                )

            command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 1
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
