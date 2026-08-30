from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.services.workspace_policy import WORKSPACE_MODULES, default_role_modules


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


def test_workspace_policy_migration_seeds_defaults_and_guards_downgrade(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0063_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()

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
            command.upgrade(config, "0062_access_roles_groups")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) "
                        "VALUES (:id, 'workspace-migration@example.com', 'hash', "
                        "'viewer', true)"
                    ),
                    {"id": user_id},
                )

            command.upgrade(config, "0063_workspace_policy")
            inspector = inspect(schema_engine)
            assert {
                "workspace_role_policies",
                "workspace_user_preferences",
            } <= set(inspector.get_table_names())
            assert {
                "ck_workspace_role_policies_role",
                "ck_workspace_role_policies_modules_object",
                "ck_workspace_role_policies_dashboard_panels_array",
                "ck_workspace_role_policies_revision",
            } <= {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "workspace_role_policies"
                )
            }
            assert {
                "ck_workspace_user_preferences_modules_object",
                "ck_workspace_user_preferences_dashboard_panels_array",
                "ck_workspace_user_preferences_revision",
            } <= {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "workspace_user_preferences"
                )
            }

            with schema_engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT role, modules_json, landing_module_id, "
                        "dashboard_panel_ids_json, revision, updated_by_user_id "
                        "FROM workspace_role_policies ORDER BY role"
                    )
                ).mappings()
                policies = {row["role"]: row for row in rows}
                assert set(policies) == {"admin", "analyst", "viewer"}
                assert set(policies["viewer"]["modules_json"]) == {
                    module.id for module in WORKSPACE_MODULES
                }
                for role, row in policies.items():
                    assert row["modules_json"] == default_role_modules(role)
                    assert row["landing_module_id"] == "primary.dashboard"
                    assert row["dashboard_panel_ids_json"] == ["rss"]
                    assert row["revision"] == 1
                    assert row["updated_by_user_id"] is None
                workspace_permission_counts = dict(
                    connection.execute(
                        text(
                            "SELECT permission, count(*) FROM iam_role_permissions "
                            "WHERE permission IN "
                            "('read:workspace', 'write:workspace_preferences') "
                            "GROUP BY permission"
                        )
                    ).all()
                )
                assert workspace_permission_counts == {
                    "read:workspace": 2,
                    "write:workspace_preferences": 2,
                }

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workspace_user_preferences "
                        "(user_id, modules_json, revision) "
                        "VALUES (:user_id, '{}', 1)"
                    ),
                    {"user_id": user_id},
                )
            with pytest.raises(RuntimeError, match="persisted workspace state"):
                command.downgrade(config, "0062_access_roles_groups")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM workspace_user_preferences "
                        "WHERE user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
                connection.execute(
                    text(
                        "UPDATE workspace_role_policies SET revision = 2 "
                        "WHERE role = 'viewer'"
                    )
                )
            with pytest.raises(RuntimeError, match="customized role policy: viewer"):
                command.downgrade(config, "0062_access_roles_groups")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE workspace_role_policies SET revision = 1, "
                        "updated_by_user_id = NULL, modules_json = CAST(:modules AS jsonb), "
                        "landing_module_id = 'primary.dashboard', "
                        "dashboard_panel_ids_json = CAST(:panels AS jsonb) "
                        "WHERE role = 'viewer'"
                    ),
                    {
                        "modules": json.dumps(default_role_modules("viewer")),
                        "panels": json.dumps(["rss"]),
                    },
                )
            command.downgrade(config, "0062_access_roles_groups")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            assert "workspace_role_policies" not in inspector.get_table_names()
            assert "workspace_user_preferences" not in inspector.get_table_names()
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM users WHERE id = :user_id"),
                        {"user_id": user_id},
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM iam_role_permissions "
                            "WHERE permission IN "
                            "('read:workspace', 'write:workspace_preferences')"
                        )
                    )
                    == 0
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
