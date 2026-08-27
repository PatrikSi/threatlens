from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TABLES = {
    "investigations",
    "investigation_members",
    "investigation_evidence",
    "investigation_notes",
    "investigation_activities",
}


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema_name},public"})
    return url.render_as_string(hide_password=False)


def test_investigation_schema_migrates_down_and_back_up(test_database_url, monkeypatch):
    schema_name = f"migration_0058_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)

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
            command.upgrade(config, "0057_system_operations")
            command.upgrade(config, "0058_investigations")

            inspector = inspect(schema_engine)
            assert _TABLES <= set(inspector.get_table_names(schema=schema_name))
            investigation_columns = {
                column["name"] for column in inspector.get_columns("investigations", schema=schema_name)
            }
            assert investigation_columns >= {
                "id",
                "title",
                "status",
                "severity",
                "visibility",
                "assignee_user_id",
                "version",
                "closed_at",
                "archived_at",
            }
            member_pk = inspector.get_pk_constraint("investigation_members", schema=schema_name)
            assert tuple(member_pk["constrained_columns"]) == ("investigation_id", "user_id")
            evidence_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints(
                    "investigation_evidence",
                    schema=schema_name,
                )
            }
            assert "uq_investigation_evidence_source" in evidence_uniques
            investigation_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("investigations", schema=schema_name)
            }
            assert investigation_checks >= {
                "ck_investigations_status",
                "ck_investigations_severity",
                "ck_investigations_visibility",
                "ck_investigations_version",
            }

            command.downgrade(config, "0057_system_operations")
            assert not (_TABLES & set(inspect(schema_engine).get_table_names(schema=schema_name)))

            command.upgrade(config, "0058_investigations")
            assert _TABLES <= set(inspect(schema_engine).get_table_names(schema=schema_name))
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
