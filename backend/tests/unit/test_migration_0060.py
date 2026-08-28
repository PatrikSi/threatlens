from __future__ import annotations

import time
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


@pytest.fixture
def iam_migration_schema(test_database_url, monkeypatch):
    schema_name = f"migration_0060_fence_{uuid.uuid4().hex}"
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
            yield schema_name, schema_engine, config
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_iam_downgrade_lock_timeout_is_bounded_and_transactional(
    iam_migration_schema,
):
    schema_name, schema_engine, config = iam_migration_schema
    retained_user_id = uuid.uuid4()
    retained_email = f"lock-timeout-{retained_user_id}@example.com"
    with schema_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_approved) "
                "VALUES (:id, :email, :password_hash, true)"
            ),
            {
                "id": retained_user_id,
                "email": retained_email,
                "password_hash": "migration-test-hash",
            },
        )

    with schema_engine.connect() as blocker:
        blocker_transaction = blocker.begin()
        try:
            blocker.execute(
                text("SELECT id FROM users WHERE id = :id"),
                {"id": retained_user_id},
            ).one()

            started_at = time.monotonic()
            with pytest.raises(RuntimeError) as error:
                command.downgrade(config, "0059_alerting_v2")
            elapsed_seconds = time.monotonic() - started_at

            assert str(error.value) == (
                "Cannot acquire exclusive IAM downgrade locks within 10 seconds. "
                "Stop all ThreatLens API and worker processes, close database "
                "transactions, and retry the downgrade."
            )
            assert 9 <= elapsed_seconds < 15

            inspector = inspect(schema_engine)
            assert _TABLES <= set(
                inspector.get_table_names(schema=schema_name)
            )
            assert "config_revision" in {
                column["name"]
                for column in inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            assert "parent_token_id" in {
                column["name"]
                for column in inspector.get_columns(
                    "api_tokens", schema=schema_name
                )
            }
            with schema_engine.connect() as verification:
                assert verification.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0060_iam_hardening"
                assert verification.scalar(
                    text("SELECT email FROM users WHERE id = :id"),
                    {"id": retained_user_id},
                ) == retained_email
        finally:
            blocker_transaction.rollback()

    command.downgrade(config, "0059_alerting_v2")

    downgraded_inspector = inspect(schema_engine)
    assert not (
        _TABLES
        & set(downgraded_inspector.get_table_names(schema=schema_name))
    )
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
    with schema_engine.connect() as verification:
        assert verification.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0059_alerting_v2"
        assert verification.scalar(
            text("SELECT email FROM users WHERE id = :id"),
            {"id": retained_user_id},
        ) == retained_email


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


def test_iam_downgrade_fences_active_and_restores_audited_token_ancestry(
    iam_migration_schema,
):
    schema_name, schema_engine, config = iam_migration_schema
    user_id = uuid.uuid4()
    parent_token_id = uuid.uuid4()
    child_token_id = uuid.uuid4()
    rollback_child_token_id = uuid.uuid4()
    with schema_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_approved) "
                "VALUES (:id, :email, :password_hash, true)"
            ),
            {
                "id": user_id,
                "email": f"delegated-token-{user_id}@example.com",
                "password_hash": "migration-test-hash",
            },
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, action, resource_type, resource_id, success, metadata_json) "
                "VALUES (:id, 'tokens.create', 'api_token', :resource_id, true, "
                "CAST(:metadata AS JSONB))"
            ),
            {
                "id": uuid.uuid4(),
                "resource_id": str(child_token_id),
                "metadata": (
                    '{"delegated_via_api_token":true,"parent_token_id":"'
                    f"{parent_token_id}"
                    '"}'
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO api_tokens "
                "(id, user_id, name, token_prefix, token_hash, scopes, expires_at) "
                "VALUES (:id, :user_id, 'parent', :prefix, :hash, '[]'::jsonb, "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {
                "id": parent_token_id,
                "user_id": user_id,
                "prefix": f"parent-{parent_token_id.hex[:12]}",
                "hash": parent_token_id.hex.ljust(64, "0"),
            },
        )
        connection.execute(
            text(
                "INSERT INTO api_tokens "
                "(id, user_id, parent_token_id, name, token_prefix, token_hash, scopes, expires_at) "
                "VALUES (:id, :user_id, :parent_id, 'child', :prefix, :hash, '[]'::jsonb, "
                "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {
                "id": child_token_id,
                "user_id": user_id,
                "parent_id": parent_token_id,
                "prefix": f"child-{child_token_id.hex[:12]}",
                "hash": child_token_id.hex.ljust(64, "0"),
            },
        )

    with pytest.raises(RuntimeError, match="delegated API tokens are active"):
        command.downgrade(config, "0059_alerting_v2")

    assert _TABLES <= set(inspect(schema_engine).get_table_names(schema=schema_name))
    with schema_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT parent_token_id FROM api_tokens WHERE id = :id"),
                {"id": child_token_id},
            )
            == parent_token_id
        )

    with schema_engine.begin() as connection:
        connection.execute(
            text("UPDATE api_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": child_token_id},
        )
    command.downgrade(config, "0059_alerting_v2")
    assert "parent_token_id" not in {
        column["name"]
        for column in inspect(schema_engine).get_columns(
            "api_tokens", schema=schema_name
        )
    }

    with schema_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO api_tokens "
                "(id, user_id, name, token_prefix, token_hash, scopes, expires_at) "
                "VALUES (:id, :user_id, 'rollback child', :prefix, :hash, "
                "'[]'::jsonb, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
            ),
            {
                "id": rollback_child_token_id,
                "user_id": user_id,
                "prefix": f"rollback-{rollback_child_token_id.hex[:12]}",
                "hash": rollback_child_token_id.hex.ljust(64, "0"),
            },
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, action, resource_type, resource_id, success, metadata_json) "
                "VALUES (:id, 'tokens.create', 'api_token', :resource_id, true, "
                "CAST(:metadata AS JSONB))"
            ),
            {
                "id": uuid.uuid4(),
                "resource_id": str(rollback_child_token_id),
                "metadata": (
                    '{"delegated_via_api_token":true,"parent_token_id":"'
                    f"{parent_token_id}"
                    '"}'
                ),
            },
        )
        connection.execute(
            text("UPDATE api_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": parent_token_id},
        )

    command.upgrade(config, "0060_iam_hardening")
    with schema_engine.connect() as connection:
        restored_parent_ids = connection.execute(
            text(
                "SELECT id, parent_token_id, revoked_at FROM api_tokens "
                "WHERE id IN (:first_child_id, :rollback_child_id)"
            ),
            {
                "first_child_id": child_token_id,
                "rollback_child_id": rollback_child_token_id,
            },
        ).all()
    assert {row.id: row.parent_token_id for row in restored_parent_ids} == {
        child_token_id: parent_token_id,
        rollback_child_token_id: parent_token_id,
    }
    assert all(row.revoked_at is not None for row in restored_parent_ids)


def test_iam_downgrade_fences_every_configured_oidc_provider(
    iam_migration_schema,
):
    schema_name, schema_engine, config = iam_migration_schema
    provider_id = uuid.uuid4()
    with schema_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO oidc_providers "
                "(id, system_key, name, issuer_url, client_id, client_auth_method, "
                "public_base_url, scopes, role_mappings_json, config_revision) "
                "VALUES (:id, 'primary', 'Migration OIDC', 'https://idp.example.com', "
                "'threatlens', 'none', 'https://threatlens.example.com', "
                "'[\"openid\"]'::json, '[]'::json, 1)"
            ),
            {"id": provider_id},
        )

    with pytest.raises(RuntimeError, match="OIDC provider is configured"):
        command.downgrade(config, "0059_alerting_v2")

    assert _TABLES <= set(inspect(schema_engine).get_table_names(schema=schema_name))
    with schema_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT config_revision FROM oidc_providers WHERE id = :id"),
                {"id": provider_id},
            )
            == 1
        )

    with schema_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM oidc_providers WHERE id = :id"), {"id": provider_id}
        )
    command.downgrade(config, "0059_alerting_v2")
