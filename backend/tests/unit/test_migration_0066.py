from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

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


def test_temporary_elevation_migration_enforces_history_and_state(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0066_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    requester_id = uuid.uuid4()
    target_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    role_id = uuid.uuid4()
    elevation_id = uuid.uuid4()

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
            command.upgrade(config, "0065_oidc_claim_mappings")
            command.upgrade(config, "0066_temporary_elevations")

            inspector = inspect(schema_engine)
            assert {
                "temporary_elevations",
                "temporary_elevation_permissions",
                "governance_operation_receipts",
            } <= set(inspector.get_table_names(schema=schema_name))
            assert "authorization_elevation_ids" in {
                column["name"]
                for column in inspector.get_columns("audit_logs", schema=schema_name)
            }
            elevation_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "temporary_elevations", schema=schema_name
                )
            }
            assert {
                "ck_temporary_elevations_state",
                "ck_temporary_elevations_no_self_decision",
                "ck_temporary_elevations_no_target_decision",
                "ck_temporary_elevations_close_actor",
            } <= elevation_checks

            with schema_engine.begin() as connection:
                for user_id, email in (
                    (requester_id, "requester@example.test"),
                    (target_id, "target@example.test"),
                    (approver_id, "approver@example.test"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, password_hash, role, is_approved) "
                            "VALUES (:id, :email, 'hash', 'viewer', true)"
                        ),
                        {"id": user_id, "email": email},
                    )
                connection.execute(
                    text(
                        "INSERT INTO iam_roles "
                        "(id, key, name, description, is_system, revision) VALUES "
                        "(:id, 'temporary-auditor', 'Temporary auditor', '', false, 1)"
                    ),
                    {"id": role_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO temporary_elevations "
                        "(id, target_user_id, target_email_snapshot, role_id, "
                        "role_key_snapshot, role_name_snapshot, role_revision_snapshot, "
                        "requested_by_user_id, requested_by_email_snapshot, "
                        "requested_duration_seconds, request_reason, "
                        "request_expires_at, status, revision) VALUES "
                        "(:id, :target_id, 'target@example.test', :role_id, "
                        "'temporary-auditor', 'Temporary auditor', 1, :requester_id, "
                        "'requester@example.test', "
                        "3600, 'Investigate an active incident', now() + interval '1 day', "
                        "'pending', 1)"
                    ),
                    {
                        "id": elevation_id,
                        "target_id": target_id,
                        "role_id": role_id,
                        "requester_id": requester_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO temporary_elevation_permissions "
                        "(elevation_id, permission) VALUES (:id, 'read:audit')"
                    ),
                    {"id": elevation_id},
                )

            with pytest.raises(IntegrityError) as self_decision_error:
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE temporary_elevations SET status = 'approved', "
                            "decided_by_user_id = :target_id, "
                            "decided_by_email_snapshot = 'target@example.test', "
                            "decided_at = now(), "
                            "decision_reason = 'self approval', grant_started_at = now(), "
                            "grant_expires_at = now() + interval '1 hour' WHERE id = :id"
                        ),
                        {"id": elevation_id, "target_id": target_id},
                    )
            assert (
                self_decision_error.value.orig.diag.constraint_name
                == "ck_temporary_elevations_no_target_decision"
            )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE temporary_elevations SET status = 'approved', "
                        "decided_by_user_id = :approver_id, "
                        "decided_by_email_snapshot = 'approver@example.test', "
                        "decided_at = now(), decision_reason = 'approved for incident', "
                        "grant_started_at = now(), "
                        "grant_expires_at = now() + interval '1 hour', "
                        "revision = revision + 1 WHERE id = :id"
                    ),
                    {"id": elevation_id, "approver_id": approver_id},
                )
                connection.execute(
                    text(
                        "UPDATE temporary_elevations SET status = 'revoked', "
                        "closed_by_principal_type = 'user', "
                        "closed_by_user_id = :requester_id, "
                        "closed_by_email_snapshot = 'requester@example.test', "
                        "closed_at = now(), close_reason = 'incident complete', "
                        "revision = revision + 1 "
                        "WHERE id = :id"
                    ),
                    {"id": elevation_id, "requester_id": requester_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM users WHERE id IN "
                        "(:requester_id, :target_id, :approver_id)"
                    ),
                    {
                        "requester_id": requester_id,
                        "target_id": target_id,
                        "approver_id": approver_id,
                    },
                )
                snapshot = connection.execute(
                    text(
                        "SELECT target_user_id, requested_by_user_id, decided_by_user_id, "
                        "closed_by_user_id, target_email_snapshot, "
                        "requested_by_email_snapshot, decided_by_email_snapshot, "
                        "closed_by_email_snapshot, status, closed_by_principal_type "
                        "FROM temporary_elevations WHERE id = :id"
                    ),
                    {"id": elevation_id},
                ).one()
                assert tuple(snapshot) == (
                    None,
                    None,
                    None,
                    None,
                    "target@example.test",
                    "requester@example.test",
                    "approver@example.test",
                    "requester@example.test",
                    "revoked",
                    "user",
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT permission FROM temporary_elevation_permissions "
                            "WHERE elevation_id = :id"
                        ),
                        {"id": elevation_id},
                    )
                    == "read:audit"
                )
                connection.execute(
                    text(
                        "INSERT INTO governance_operation_receipts "
                        "(id, actor_user_id, operation, key_hash, request_fingerprint, "
                        "resource_type, resource_id, response_json, http_status) VALUES "
                        "(:id, :actor_id, 'elevation.create', :key_hash, :fingerprint, "
                        "'temporary_elevation', :resource_id, '{}'::jsonb, 201)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "actor_id": requester_id,
                        "key_hash": "a" * 64,
                        "fingerprint": "b" * 64,
                        "resource_id": elevation_id,
                    },
                )

            with pytest.raises(RuntimeError, match="governance history would be lost"):
                command.downgrade(config, "0065_oidc_claim_mappings")

            with schema_engine.begin() as connection:
                connection.execute(text("DELETE FROM governance_operation_receipts"))
                connection.execute(text("DELETE FROM temporary_elevations"))
            command.downgrade(config, "0065_oidc_claim_mappings")
            inspector = inspect(schema_engine)
            assert "temporary_elevations" not in inspector.get_table_names(
                schema=schema_name
            )
            assert "authorization_elevation_ids" not in {
                column["name"]
                for column in inspector.get_columns("audit_logs", schema=schema_name)
            }
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
