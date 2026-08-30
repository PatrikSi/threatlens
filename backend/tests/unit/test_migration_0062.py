from __future__ import annotations

import uuid
from pathlib import Path

import pytest
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


def test_access_roles_groups_migration_validates_legacy_roles_and_round_trips(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0062_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    enriched_audit_id = uuid.uuid4()
    credential_id = uuid.uuid4()

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
            migration_env.setenv("DATABASE_URL", schema_database_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0061_alert_dispatch_publication")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) "
                        "VALUES (:id, 'legacy-role@example.com', 'hash', 'operator', true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, actor_user_id, action, resource_type, success, metadata_json) "
                        "VALUES (:id, :user_id, 'legacy.action', 'user', true, '{}')"
                    ),
                    {"id": audit_id, "user_id": user_id},
                )

            with pytest.raises(RuntimeError, match="unsupported legacy role"):
                command.upgrade(config, "0062_access_roles_groups")

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE users SET role = 'viewer' WHERE id = :id"),
                    {"id": user_id},
                )
            command.upgrade(config, "0062_access_roles_groups")

            inspector = inspect(schema_engine)
            assert {
                "iam_policy_state",
                "iam_roles",
                "iam_role_permissions",
                "iam_user_role_assignments",
                "iam_groups",
                "iam_group_memberships",
                "iam_group_role_assignments",
            } <= set(inspector.get_table_names(schema=schema_name))
            audit_columns = {
                column["name"]
                for column in inspector.get_columns("audit_logs", schema=schema_name)
            }
            assert {
                "actor_principal_type",
                "actor_principal_id",
                "credential_kind",
                "credential_id",
                "request_id",
                "source_ip",
            } <= audit_columns
            audit_indexes = {
                index["name"]
                for index in inspector.get_indexes("audit_logs", schema=schema_name)
            }
            assert {
                "ix_audit_logs_actor_principal",
                "ix_audit_logs_credential_created",
                "ix_audit_logs_request_id",
                "ix_audit_logs_resource_created",
                "ix_audit_logs_success_created",
            } <= audit_indexes
            assignment_constraints = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "iam_user_role_assignments", schema=schema_name
                )
            }
            assert "ck_iam_user_role_assignments_source_key" in assignment_constraints

            with schema_engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM iam_roles")) == 3
                assert connection.scalar(text("SELECT count(*) FROM iam_groups")) == 1
                audit = connection.execute(
                    text(
                        "SELECT actor_principal_type, actor_principal_id "
                        "FROM audit_logs WHERE id = :id"
                    ),
                    {"id": audit_id},
                ).one()
                assert audit.actor_principal_type is None
                assert audit.actor_principal_id is None

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, actor_user_id, actor_principal_type, actor_principal_id, "
                        "credential_kind, credential_id, request_id, source_ip, action, "
                        "resource_type, success, metadata_json) VALUES "
                        "(:id, :user_id, 'user', :user_id, 'api_token', :credential_id, "
                        "'migration-audit', '192.0.2.10', 'iam.test', 'iam_role', true, '{}')"
                    ),
                    {
                        "id": enriched_audit_id,
                        "user_id": user_id,
                        "credential_id": credential_id,
                    },
                )

            custom_role_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO iam_roles "
                        "(id, key, name, description, is_system, revision) "
                        "VALUES (:id, 'custom', 'Custom', '', false, 1)"
                    ),
                    {"id": custom_role_id},
                )
            with pytest.raises(
                RuntimeError, match="custom roles, groups, or assignments"
            ):
                command.downgrade(config, "0061_alert_dispatch_publication")

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM iam_roles WHERE id = :id"),
                    {"id": custom_role_id},
                )
            command.downgrade(config, "0061_alert_dispatch_publication")
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == ("0061_alert_dispatch_publication")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            assert "iam_roles" not in inspector.get_table_names(schema=schema_name)
            assert "actor_principal_type" not in {
                column["name"]
                for column in inspector.get_columns("audit_logs", schema=schema_name)
            }
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM users WHERE id = :id"),
                        {"id": user_id},
                    )
                    == 1
                )
                preserved_context = connection.scalar(
                    text(
                        "SELECT metadata_json -> '_access_context' FROM audit_logs "
                        "WHERE id = :id"
                    ),
                    {"id": enriched_audit_id},
                )
                assert preserved_context == {
                    "actor_principal_type": "user",
                    "actor_principal_id": str(user_id),
                    "credential_kind": "api_token",
                    "credential_id": str(credential_id),
                    "request_id": "migration-audit",
                    "source_ip": "192.0.2.10",
                }
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
