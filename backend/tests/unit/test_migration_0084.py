from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _BACKEND_DIR / "alembic/versions/0084_ioc_candidate_search.py"


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


def _index_definition(
    engine,
    schema_name: str,
    *,
    table_name: str,
    index_name: str,
) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = :schema_name "
                "AND tablename = :table_name "
                "AND indexname = :index_name"
            ),
            {
                "schema_name": schema_name,
                "table_name": table_name,
                "index_name": index_name,
            },
        )


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "test_migration_0084_ioc_candidate_search",
        _MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ioc_candidate_search_migration_is_self_contained():
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert "from app." not in source
    assert "\nimport app." not in source
    assert 'down_revision = "0083_system_health_history"' in source


def test_ioc_candidate_search_migration_upgrades_and_downgrades(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0084_{uuid.uuid4().hex}"
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

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0083_system_health_history")
            assert (
                _index_definition(
                    schema_engine,
                    schema_name,
                    table_name="iocs",
                    index_name="ix_iocs_value_norm_trgm",
                )
                is None
            )

            command.upgrade(config, "0084_ioc_candidate_search")
            ioc_index = _index_definition(
                schema_engine,
                schema_name,
                table_name="iocs",
                index_name="ix_iocs_value_norm_trgm",
            )
            assert ioc_index is not None
            assert "USING gin" in ioc_index
            assert "gin_trgm_ops" in ioc_index
            assert "lower(" in ioc_index

            report_index = _index_definition(
                schema_engine,
                schema_name,
                table_name="reports",
                index_name="ix_reports_observed_at",
            )
            assert report_index is not None
            assert "coalesce(generated_at, created_at)" in report_index.lower()

            alert_index = _index_definition(
                schema_engine,
                schema_name,
                table_name="alert_occurrences",
                index_name="ix_alert_occurrences_owner_created",
            )
            assert alert_index is not None
            assert "(owner_user_id, created_at)" in alert_index.lower()

            command.downgrade(config, "0083_system_health_history")
            for table_name, index_name in (
                ("iocs", "ix_iocs_value_norm_trgm"),
                ("reports", "ix_reports_observed_at"),
                (
                    "alert_occurrences",
                    "ix_alert_occurrences_owner_created",
                ),
            ):
                assert (
                    _index_definition(
                        schema_engine,
                        schema_name,
                        table_name=table_name,
                        index_name=index_name,
                    )
                    is None
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_ioc_candidate_search_migration_supports_sqlite(monkeypatch):
    migration = _load_migration_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE iocs (value_norm TEXT NOT NULL)"))
            connection.execute(
                text(
                    "CREATE TABLE reports ("
                    "generated_at DATETIME, created_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE alert_occurrences ("
                    "owner_user_id TEXT NOT NULL, created_at DATETIME NOT NULL)"
                )
            )
            operations = Operations(MigrationContext.configure(connection))
            monkeypatch.setattr(migration, "op", operations)

            migration.upgrade()
            definitions = dict(
                connection.execute(
                    text(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type = 'index' AND name IN ("
                        "'ix_iocs_value_norm_trgm', "
                        "'ix_reports_observed_at', "
                        "'ix_alert_occurrences_owner_created')"
                    )
                ).all()
            )
            assert set(definitions) == {
                "ix_iocs_value_norm_trgm",
                "ix_reports_observed_at",
                "ix_alert_occurrences_owner_created",
            }
            assert "lower(value_norm)" in definitions["ix_iocs_value_norm_trgm"].lower()
            assert (
                "coalesce(generated_at, created_at)"
                in definitions["ix_reports_observed_at"].lower()
            )
            assert (
                "owner_user_id, created_at"
                in definitions["ix_alert_occurrences_owner_created"].lower()
            )

            migration.downgrade()
            remaining = connection.scalar(
                text(
                    "SELECT count(*) FROM sqlite_master "
                    "WHERE type = 'index' AND name IN ("
                    "'ix_iocs_value_norm_trgm', "
                    "'ix_reports_observed_at', "
                    "'ix_alert_occurrences_owner_created')"
                )
            )
            assert remaining == 0
    finally:
        engine.dispose()
