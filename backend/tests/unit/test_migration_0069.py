from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID


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


def test_data_policy_migration_backfills_and_protects_unrestricted_label(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0069_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    existing_feed_id = uuid.uuid4()
    old_process_feed_id = uuid.uuid4()

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
            command.upgrade(config, "0068_access_reviews")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO feeds (id, name, url, url_digest) "
                        "VALUES (:id, 'Existing feed', 'encrypted', :digest)"
                    ),
                    {"id": existing_feed_id, "digest": "a" * 64},
                )

            command.upgrade(config, "0069_data_policy_foundation")
            inspector = inspect(schema_engine)
            assert {
                "data_policy_state",
                "handling_labels",
                "data_policy_role_grants",
            } <= set(inspector.get_table_names(schema=schema_name))
            assert "handling_label_id" in {
                column["name"]
                for column in inspector.get_columns("feeds", schema=schema_name)
            }

            with schema_engine.begin() as connection:
                assert connection.execute(
                    text(
                        "SELECT mode, revision, coverage_version "
                        "FROM data_policy_state WHERE id = 1"
                    )
                ).one() == ("disabled", 1, 0)
                unrestricted = connection.execute(
                    text(
                        "SELECT key, is_unrestricted, is_system, is_active "
                        "FROM handling_labels WHERE id = :id"
                    ),
                    {"id": UNRESTRICTED_HANDLING_LABEL_ID},
                ).one()
                assert unrestricted == ("unrestricted", True, True, True)
                assert (
                    connection.scalar(
                        text("SELECT handling_label_id FROM feeds WHERE id = :id"),
                        {"id": existing_feed_id},
                    )
                    == UNRESTRICTED_HANDLING_LABEL_ID
                )

                # This intentionally uses the pre-0069 insert shape. The server
                # default protects rolling upgrades with an older API process.
                connection.execute(
                    text(
                        "INSERT INTO feeds (id, name, url, url_digest) "
                        "VALUES (:id, 'Old process feed', 'encrypted', :digest)"
                    ),
                    {"id": old_process_feed_id, "digest": "b" * 64},
                )
                assert (
                    connection.scalar(
                        text("SELECT handling_label_id FROM feeds WHERE id = :id"),
                        {"id": old_process_feed_id},
                    )
                    == UNRESTRICTED_HANDLING_LABEL_ID
                )

            with pytest.raises(DBAPIError, match="identity is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE handling_labels SET is_active = false "
                            "WHERE id = :id"
                        ),
                        {"id": UNRESTRICTED_HANDLING_LABEL_ID},
                    )
            with pytest.raises(DBAPIError, match="cannot be deleted"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM handling_labels WHERE id = :id"),
                        {"id": UNRESTRICTED_HANDLING_LABEL_ID},
                    )
            with pytest.raises(DBAPIError, match="policy state cannot be deleted"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM data_policy_state WHERE id = 1")
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET revision = 2 WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="policy state has been changed"):
                command.downgrade(config, "0068_access_reviews")

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET revision = 1 WHERE id = 1")
                )
            command.downgrade(config, "0068_access_reviews")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            assert "data_policy_state" not in inspector.get_table_names(
                schema=schema_name
            )
            assert "handling_label_id" not in {
                column["name"]
                for column in inspector.get_columns("feeds", schema=schema_name)
            }
            with schema_engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM feeds")) == 2
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
