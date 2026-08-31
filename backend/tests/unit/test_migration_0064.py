from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
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
    url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    return url.render_as_string(hide_password=False)


def test_service_account_migration_constraints_and_guarded_downgrade(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0064_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()
    role_id = uuid.uuid4()
    account_id = uuid.uuid4()
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
            command.upgrade(config, "0063_workspace_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) "
                        "VALUES (:id, 'service-account-migration@example.com', "
                        "'hash', 'admin', true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_roles "
                        "(id, key, name, description, is_system, revision, "
                        "created_by_user_id) VALUES "
                        "(:id, 'migration-machine-role', 'Migration machine role', "
                        "'', false, 1, :user_id)"
                    ),
                    {"id": role_id, "user_id": user_id},
                )

            command.upgrade(config, "0064_service_accounts")
            inspector = inspect(schema_engine)
            assert {
                "service_accounts",
                "service_account_credentials",
                "service_account_role_assignments",
            } <= set(inspector.get_table_names(schema=schema_name))
            assert {
                "ck_service_accounts_revision",
                "ck_service_accounts_active_state",
            } <= {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "service_accounts", schema=schema_name
                )
            }
            assert {
                "ck_service_account_credentials_prefix",
                "ck_service_account_credentials_hash_length",
                "ck_service_account_credentials_operation_key_hash_length",
                "ck_service_account_credentials_operation_request_hash_length",
                "ck_service_account_credentials_operation_receipt",
                "ck_service_account_credentials_scopes_array",
                "ck_service_account_credentials_expiry",
                "ck_service_account_credentials_original_expiry",
            } <= {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "service_account_credentials", schema=schema_name
                )
            }
            assert {
                "uq_service_account_credentials_prefix",
                "uq_service_account_credentials_hash",
                "uq_service_account_credentials_operation_key",
            } <= {
                constraint["name"]
                for constraint in inspector.get_unique_constraints(
                    "service_account_credentials", schema=schema_name
                )
            }
            assert {
                "ix_service_account_credentials_account_created",
                "ix_service_account_credentials_active_expiry",
            } <= {
                index["name"]
                for index in inspector.get_indexes(
                    "service_account_credentials", schema=schema_name
                )
            }

            now = datetime.now(timezone.utc)
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO service_accounts "
                        "(id, key, name, description, created_by_user_id) VALUES "
                        "(:id, 'migration-agent', 'Migration agent', '', :user_id)"
                    ),
                    {"id": account_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO service_account_role_assignments "
                        "(id, service_account_id, role_id, assigned_by_user_id) "
                        "VALUES (:id, :account_id, :role_id, :user_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "account_id": account_id,
                        "role_id": role_id,
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO service_account_credentials "
                        "(id, service_account_id, name, token_prefix, token_hash, "
                        "scopes, expires_at, created_by_user_id) VALUES "
                        "(:id, :account_id, 'migration credential', "
                        "'tlsa_0123456789abcdef', :token_hash, CAST(:scopes AS jsonb), "
                        ":expires_at, :user_id)"
                    ),
                    {
                        "id": credential_id,
                        "account_id": account_id,
                        "token_hash": "a" * 64,
                        "scopes": json.dumps(["read:items"]),
                        "expires_at": now + timedelta(days=30),
                        "user_id": user_id,
                    },
                )

            with pytest.raises(RuntimeError, match="non-human identity state"):
                command.downgrade(config, "0063_workspace_policy")

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO service_account_credentials "
                            "(id, service_account_id, name, token_prefix, token_hash, "
                            "scopes, expires_at) VALUES "
                            "(:id, :account_id, 'invalid prefix', 'tl_bad', "
                            ":token_hash, CAST(:scopes AS jsonb), :expires_at)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "account_id": account_id,
                            "token_hash": "b" * 64,
                            "scopes": json.dumps(["read:items"]),
                            "expires_at": now + timedelta(days=1),
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO service_account_credentials "
                            "(id, service_account_id, name, token_prefix, token_hash, "
                            "scopes, expires_at) VALUES "
                            "(:id, :account_id, 'invalid scopes', "
                            "'tlsa_1123456789abcdef', :token_hash, "
                            "CAST(:scopes AS jsonb), :expires_at)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "account_id": account_id,
                            "token_hash": "c" * 64,
                            "scopes": json.dumps([1]),
                            "expires_at": now + timedelta(days=1),
                        },
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM service_accounts WHERE id = :id"),
                    {"id": account_id},
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM service_account_credentials "
                            "WHERE service_account_id = :id"
                        ),
                        {"id": account_id},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM service_account_role_assignments "
                            "WHERE service_account_id = :id"
                        ),
                        {"id": account_id},
                    )
                    == 0
                )

            command.downgrade(config, "0063_workspace_policy")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            schema_tables = inspector.get_table_names(schema=schema_name)
            assert "service_accounts" not in schema_tables
            assert "service_account_credentials" not in schema_tables
            assert "service_account_role_assignments" not in schema_tables
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            f'SELECT count(*) FROM "{schema_name}".iam_roles '
                            "WHERE id = :id"
                        ),
                        {"id": role_id},
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text(
                            f'SELECT count(*) FROM "{schema_name}".users WHERE id = :id'
                        ),
                        {"id": user_id},
                    )
                    == 1
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
