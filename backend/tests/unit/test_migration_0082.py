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
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_audit_identity_snapshot_migration_backfills_only_unambiguous_labels(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0082_{uuid.uuid4().hex}"
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

    user_id = uuid.uuid4()
    service_account_id = uuid.uuid4()
    deleted_login_actor_id = uuid.uuid4()
    deleted_admin_id = uuid.uuid4()
    user_audit_id = uuid.uuid4()
    service_audit_id = uuid.uuid4()
    login_audit_id = uuid.uuid4()
    historical_login_id = uuid.uuid4()
    create_audit_id = uuid.uuid4()
    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0081_data_policy_activation")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) "
                        "VALUES "
                        "(:id, 'operator@example.com', 'hash', 'admin', true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO service_accounts (id, key, name, description) "
                        "VALUES (:id, 'collector', 'Collector service', '')"
                    ),
                    {"id": service_account_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, actor_user_id, actor_principal_type, actor_principal_id, "
                        "action, resource_type, resource_id, success, metadata_json) "
                        "VALUES "
                        "(:user_audit, :user_id, NULL, NULL, 'users.update', "
                        "'user', :user_resource, true, '{}'::jsonb), "
                        "(:service_audit, NULL, 'service_account', :service_id, "
                        "'feeds.read', 'service_account', :service_resource, true, '{}'::jsonb), "
                        "(:login_audit, NULL, 'user', :deleted_login_actor, "
                        "'auth.login', 'user', :deleted_login_resource, true, "
                        "'{\"email\": \"former@example.com\"}'::jsonb), "
                        "(:historical_login, :user_id, 'user', :user_id, "
                        "'auth.login', 'user', :user_resource, true, "
                        "'{\"email\": \"operator-before-rename@example.com\"}'::jsonb), "
                        "(:create_audit, NULL, 'user', :deleted_admin, "
                        "'users.create', 'user', :user_resource, true, "
                        "'{\"email\": \"operator@example.com\"}'::jsonb)"
                    ),
                    {
                        "user_audit": user_audit_id,
                        "user_id": user_id,
                        "user_resource": str(user_id),
                        "service_audit": service_audit_id,
                        "service_id": service_account_id,
                        "service_resource": str(service_account_id),
                        "login_audit": login_audit_id,
                        "historical_login": historical_login_id,
                        "deleted_login_actor": deleted_login_actor_id,
                        "deleted_login_resource": str(deleted_login_actor_id),
                        "create_audit": create_audit_id,
                        "deleted_admin": deleted_admin_id,
                    },
                )

            command.upgrade(config, "0082_audit_identity_snapshots")
            with schema_engine.connect() as connection:
                rows = {
                    row.id: row
                    for row in connection.execute(
                        text(
                            "SELECT id, actor_principal_type, actor_principal_id, "
                            "actor_label_snapshot, resource_label_snapshot "
                            "FROM audit_logs WHERE id IN "
                            "(:user_audit, :service_audit, :login_audit, "
                            ":historical_login, :create_audit)"
                        ),
                        {
                            "user_audit": user_audit_id,
                            "service_audit": service_audit_id,
                            "login_audit": login_audit_id,
                            "historical_login": historical_login_id,
                            "create_audit": create_audit_id,
                        },
                    ).all()
                }
            assert rows[user_audit_id].actor_label_snapshot == "operator@example.com"
            assert rows[user_audit_id].resource_label_snapshot == "operator@example.com"
            assert rows[user_audit_id].actor_principal_type == "user"
            assert rows[user_audit_id].actor_principal_id == user_id
            assert rows[service_audit_id].actor_label_snapshot == "Collector service"
            assert rows[service_audit_id].resource_label_snapshot == "Collector service"
            assert rows[login_audit_id].actor_label_snapshot == "former@example.com"
            assert rows[login_audit_id].resource_label_snapshot == "former@example.com"
            assert rows[historical_login_id].actor_label_snapshot == (
                "operator-before-rename@example.com"
            )
            assert rows[historical_login_id].resource_label_snapshot == (
                "operator-before-rename@example.com"
            )
            assert rows[create_audit_id].actor_label_snapshot is None
            assert rows[create_audit_id].resource_label_snapshot == "operator@example.com"

            command.downgrade(config, "0081_data_policy_activation")
            assert {
                column["name"] for column in inspect(schema_engine).get_columns("audit_logs")
            }.isdisjoint({"actor_label_snapshot", "resource_label_snapshot"})
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
