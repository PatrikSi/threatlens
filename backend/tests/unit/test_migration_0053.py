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
    url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    return url.render_as_string(hide_password=False)


def test_report_operation_receipts_migrate_and_downgrade(test_database_url, monkeypatch):
    schema_name = f"migration_0053_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()
    receipt_id = uuid.uuid4()
    resource_id = uuid.uuid4()

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
            command.upgrade(config, "0052_legacy_worker_guard")
            command.upgrade(config, "0053_report_operation_receipts")

            with schema_engine.connect() as connection:
                inspector = inspect(connection)
                columns = {
                    column["name"]
                    for column in inspector.get_columns("report_operation_receipts")
                }
                unique_constraints = {
                    constraint["name"]: tuple(constraint["column_names"])
                    for constraint in inspector.get_unique_constraints(
                        "report_operation_receipts"
                    )
                }
                indexes = {
                    index["name"]: tuple(index["column_names"])
                    for index in inspector.get_indexes("report_operation_receipts")
                }

            assert columns == {
                "id",
                "actor_user_id",
                "operation",
                "key_hash",
                "fingerprint",
                "resource_type",
                "resource_id",
                "created_at",
            }
            assert unique_constraints[
                "uq_report_operation_receipts_actor_key"
            ] == ("actor_user_id", "key_hash")
            assert indexes["ix_report_operation_receipts_resource"] == (
                "resource_type",
                "resource_id",
            )
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, is_approved) "
                        "VALUES (:id, :email, 'not-a-login-secret', true)"
                    ),
                    {
                        "id": user_id,
                        "email": f"migration-0053-{user_id}@example.com",
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO report_operation_receipts (
                            id, actor_user_id, operation, key_hash, fingerprint,
                            resource_type, resource_id
                        ) VALUES (
                            :id, :user_id, 'report:template:create',
                            :key_hash, :fingerprint, 'report_template', :resource_id
                        )
                        """
                    ),
                    {
                        "id": receipt_id,
                        "user_id": user_id,
                        "key_hash": "a" * 64,
                        "fingerprint": "b" * 64,
                        "resource_id": resource_id,
                    },
                )

            command.downgrade(config, "0052_legacy_worker_guard")
            with schema_engine.connect() as connection:
                assert inspect(connection).has_table(
                    "report_operation_receipts",
                    schema=schema_name,
                )
                assert connection.scalar(
                    text(
                        "SELECT resource_id FROM report_operation_receipts "
                        "WHERE id = :id"
                    ),
                    {"id": receipt_id},
                ) == resource_id

            command.upgrade(config, "0053_report_operation_receipts")
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text(
                        "SELECT resource_id FROM report_operation_receipts "
                        "WHERE id = :id"
                    ),
                    {"id": receipt_id},
                ) == resource_id
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
