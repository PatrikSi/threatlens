from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TABLES = {
    "auth_sessions",
    "user_totp_credentials",
    "user_recovery_codes",
    "mfa_login_challenges",
}


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    return url.render_as_string(hide_password=False)


def test_iam_schema_migrates_down_and_back_up(test_database_url, monkeypatch):
    schema_name = f"migration_0060_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY)'
            )
        )

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_database_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0059_alerting_v2")
            command.upgrade(config, "0060_iam_hardening")

            inspector = inspect(schema_engine)
            assert _TABLES <= set(inspector.get_table_names(schema=schema_name))
            session_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "auth_sessions", schema=schema_name
                )
            }
            assert session_checks >= {
                "ck_auth_sessions_auth_method",
                "ck_auth_sessions_mfa_method",
            }
            session_columns = {
                column["name"]
                for column in inspector.get_columns("auth_sessions", schema=schema_name)
            }
            assert {
                "auth_token_version",
                "identity_authenticated_at",
                "identity_acr",
                "identity_amr_json",
            } <= session_columns
            totp_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "user_totp_credentials", schema=schema_name
                )
            }
            assert {
                "enrollment_session_id",
                "enrollment_auth_token_version",
            } <= totp_columns
            recovery_hash_column = next(
                column
                for column in inspector.get_columns(
                    "user_recovery_codes", schema=schema_name
                )
                if column["name"] == "code_hash"
            )
            assert recovery_hash_column["type"].length == 128
            oidc_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            token_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "api_tokens", schema=schema_name
                )
            }
            assert "config_revision" in oidc_columns
            assert "parent_token_id" in token_columns
            challenge_indexes = {
                index["name"]
                for index in inspector.get_indexes(
                    "mfa_login_challenges", schema=schema_name
                )
            }
            assert "ix_mfa_login_challenges_expiry" in challenge_indexes
            challenge_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "mfa_login_challenges", schema=schema_name
                )
            }
            assert "auth_token_version" in challenge_columns

            command.downgrade(config, "0059_alerting_v2")
            assert not (
                _TABLES
                & set(inspect(schema_engine).get_table_names(schema=schema_name))
            )
            downgraded_inspector = inspect(schema_engine)
            assert "config_revision" not in {
                column["name"]
                for column in downgraded_inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            assert "parent_token_id" not in {
                column["name"]
                for column in downgraded_inspector.get_columns(
                    "api_tokens", schema=schema_name
                )
            }
            command.upgrade(config, "0060_iam_hardening")
            assert _TABLES <= set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )

            user_id = uuid.uuid4()
            credential_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, is_approved) "
                        "VALUES (:id, :email, :password_hash, true)"
                    ),
                    {
                        "id": user_id,
                        "email": f"migration-{user_id}@example.com",
                        "password_hash": "migration-test-hash",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO user_totp_credentials "
                        "(id, user_id, secret_encrypted, status) "
                        "VALUES (:id, :user_id, :secret, 'active')"
                    ),
                    {
                        "id": credential_id,
                        "user_id": user_id,
                        "secret": "enc:v1:migration-test",
                    },
                )

            with pytest.raises(RuntimeError, match="local MFA is active"):
                command.downgrade(config, "0059_alerting_v2")
            assert _TABLES <= set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )

            with schema_engine.begin() as connection:
                connection.execute(text("DELETE FROM user_totp_credentials"))
            command.downgrade(config, "0059_alerting_v2")
            command.upgrade(config, "0060_iam_hardening")
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
