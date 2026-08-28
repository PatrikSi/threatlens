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


def test_alert_dispatch_publication_migration_round_trip(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0061_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    request_id = uuid.uuid4()

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
            command.upgrade(config, "0060_iam_hardening")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO alert_evaluation_requests "
                        "(id, item_id, item_content_hash) VALUES "
                        "(:id, :item_id, :content_hash)"
                    ),
                    {
                        "id": request_id,
                        "item_id": uuid.uuid4(),
                        "content_hash": "a" * 64,
                    },
                )

            command.upgrade(config, "0061_alert_dispatch_publication")
            inspector = inspect(schema_engine)
            columns = {
                column["name"]
                for column in inspector.get_columns("alert_evaluation_requests")
            }
            indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes("alert_evaluation_requests")
            }
            assert "dispatch_published_at" in columns
            assert indexes["ix_alert_evaluation_requests_dispatch_publication"] == (
                "state",
                "dispatch_published_at",
                "available_at",
            )
            with schema_engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT id, dispatch_published_at "
                        "FROM alert_evaluation_requests WHERE id = :id"
                    ),
                    {"id": request_id},
                ).one()
                assert row.id == request_id
                assert row.dispatch_published_at is None

            command.downgrade(config, "0060_iam_hardening")
            inspector = inspect(schema_engine)
            columns = {
                column["name"]
                for column in inspector.get_columns("alert_evaluation_requests")
            }
            indexes = {
                index["name"]
                for index in inspector.get_indexes("alert_evaluation_requests")
            }
            assert "dispatch_published_at" not in columns
            assert "ix_alert_evaluation_requests_dispatch_publication" not in indexes
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM alert_evaluation_requests "
                            "WHERE id = :id"
                        ),
                        {"id": request_id},
                    )
                    == 1
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
