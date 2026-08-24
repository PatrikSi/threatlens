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


def test_feed_fetch_fence_migration_round_trip(test_database_url, monkeypatch):
    schema_name = f"migration_0048_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    feed_id = uuid.uuid4()

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
            command.upgrade(config, "0047_report_dispatch")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO feeds (id, name, url, url_digest)
                        VALUES (:id, 'Legacy feed', :url, :digest)
                        """
                    ),
                    {
                        "id": feed_id,
                        "url": "https://example.com/legacy.xml",
                        "digest": uuid.uuid4().hex,
                    },
                )

            command.upgrade(config, "0048_feed_fetch_fence")
            with schema_engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                fetch_fence = connection.scalar(
                    text("SELECT fetch_fence FROM feeds WHERE id = :id"),
                    {"id": feed_id},
                )
            assert revision == "0048_feed_fetch_fence"
            assert fetch_fence == 0

            command.downgrade(config, "0047_report_dispatch")
            column_names = {
                column["name"]
                for column in inspect(schema_engine).get_columns("feeds")
            }
            assert "fetch_fence" not in column_names

            command.upgrade(config, "0048_feed_fetch_fence")
            with schema_engine.connect() as connection:
                restored_fence = connection.scalar(
                    text("SELECT fetch_fence FROM feeds WHERE id = :id"),
                    {"id": feed_id},
                )
            assert restored_fence == 0
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()
