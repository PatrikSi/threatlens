from __future__ import annotations

import uuid
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
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_system_health_history_migration_is_self_contained():
    source = (
        _BACKEND_DIR / "alembic/versions/0083_system_health_history.py"
    ).read_text(encoding="utf-8")

    assert "from app." not in source
    assert "\nimport app." not in source
    assert 'down_revision = "0082_audit_identity_snapshots"' in source


def test_system_health_history_migration_upgrades_enforces_and_downgrades(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0083_{uuid.uuid4().hex}"
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
            command.upgrade(config, "0082_audit_identity_snapshots")
            command.upgrade(config, "0083_system_health_history")

            table_names = inspect(schema_engine).get_table_names(schema=schema_name)
            assert "system_health_samples" in table_names
            indexes = inspect(schema_engine).get_indexes(
                "system_health_samples",
                schema=schema_name,
            )
            assert {
                (index["name"], index["unique"])
                for index in indexes
            } >= {("ix_system_health_samples_sampled_at", True)}

            first_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO system_health_samples "
                        "(id, sampled_at, overall_status, worker_status, "
                        "worker_reason) VALUES "
                        "(:id, '2026-09-01T12:00:00Z', 'degraded', "
                        "'degraded', 'canary_dispatch_unavailable')"
                    ),
                    {"id": first_id},
                )
                assert connection.scalar(
                    text("SELECT count(*) FROM system_health_samples")
                ) == 1
                assert connection.scalar(
                    text("SELECT worker_reason FROM system_health_samples")
                ) == "canary_dispatch_unavailable"
                assert connection.scalar(
                    text("SELECT active_count FROM system_health_samples")
                ) is None

            with pytest.raises(IntegrityError), schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO system_health_samples "
                        "(id, sampled_at, overall_status, worker_status, "
                        "worker_reason) VALUES "
                        "(:id, '2026-09-01T12:00:00Z', 'healthy', "
                        "'healthy', 'healthy')"
                    ),
                    {"id": uuid.uuid4()},
                )

            with pytest.raises(IntegrityError), schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO system_health_samples "
                        "(id, sampled_at, overall_status, worker_status, "
                        "worker_reason) VALUES "
                        "(:id, '2026-09-01T12:05:00Z', 'healthy', "
                        "'healthy', 'not_stable')"
                    ),
                    {"id": uuid.uuid4()},
                )

            command.downgrade(config, "0082_audit_identity_snapshots")
            assert "system_health_samples" not in inspect(schema_engine).get_table_names(
                schema=schema_name
            )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
