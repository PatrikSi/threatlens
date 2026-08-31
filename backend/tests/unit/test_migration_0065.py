from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.iam import IAMGroupMembership, IAMUserRoleAssignment
from app.models.oidc import ExternalIdentity, OIDCProvider
from app.models.oidc_access import (
    OIDCAccessPolicy,
    OIDCClaimMappingSet,
    OIDCGroupClaimMapping,
    OIDCRoleClaimMapping,
)
from app.schemas.oidc_access import (
    OIDCAccessPolicyResponse,
    OIDCClaimMappingSetCreateRequest,
    OIDCClaimMappingSetUpdateRequest,
    OIDCGroupValueMappingRequest,
    OIDCRoleValueMappingRequest,
)
from scripts.prepare_oidc_access_upgrade import _convert_to_local


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


def _constraint_names(inspector, table_name: str, schema_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            table_name,
            schema=schema_name,
        )
    }


def _foreign_key_names(inspector, table_name: str, schema_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_foreign_keys(
            table_name,
            schema=schema_name,
        )
    }


def _unique_names(inspector, table_name: str, schema_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            table_name,
            schema=schema_name,
        )
    }


def _index_names(inspector, table_name: str, schema_name: str) -> set[str]:
    return {
        index["name"] for index in inspector.get_indexes(table_name, schema=schema_name)
    }


def test_oidc_claim_mapping_migration_enforces_targets_and_downgrades_cleanly(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0065_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    mapping_set_id = uuid.uuid4()
    custom_role_id = uuid.uuid4()
    custom_group_id = uuid.uuid4()
    role_mapping_id = uuid.uuid4()
    group_mapping_id = uuid.uuid4()
    mapped_role_assignment_id = uuid.uuid4()
    mapped_group_membership_id = uuid.uuid4()

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
            command.upgrade(config, "0064_service_accounts")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) VALUES "
                        "(:id, 'oidc-access-migration@example.com', 'hash', "
                        "'admin', true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_providers "
                        "(id, system_key, name, issuer_url, client_id, "
                        "public_base_url, updated_by_user_id) VALUES "
                        "(:id, 'primary', 'Migration SSO', "
                        "'https://identity.example.test/application/o/threatlens/', "
                        "'migration-client', 'https://threatlens.example.test', "
                        ":user_id)"
                    ),
                    {"id": provider_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO external_identities "
                        "(id, provider_id, user_id, issuer, subject, email_at_link) "
                        "VALUES (:id, :provider_id, :user_id, "
                        "'https://identity.example.test/application/o/threatlens/', "
                        "'migration-subject', 'oidc-access-migration@example.com')"
                    ),
                    {
                        "id": identity_id,
                        "provider_id": provider_id,
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_roles "
                        "(id, key, name, description, is_system, revision, "
                        "created_by_user_id) VALUES "
                        "(:id, 'oidc-migration-role', 'OIDC migration role', '', "
                        "false, 1, :user_id)"
                    ),
                    {"id": custom_role_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_groups "
                        "(id, key, name, description, source, external_key, "
                        "is_system, revision, created_by_user_id) VALUES "
                        "(:id, 'oidc-migration-group', 'OIDC migration group', '', "
                        "'local', NULL, false, 1, :user_id)"
                    ),
                    {"id": custom_group_id, "user_id": user_id},
                )
                system_role_id = connection.scalar(
                    text(
                        "SELECT id FROM iam_roles WHERE is_system ORDER BY key LIMIT 1"
                    )
                )
                system_group_id = connection.scalar(
                    text(
                        "SELECT id FROM iam_groups WHERE is_system ORDER BY key LIMIT 1"
                    )
                )

            assert system_role_id is not None
            assert system_group_id is not None
            command.upgrade(config, "0065_oidc_claim_mappings")

            inspector = inspect(schema_engine)
            assert {
                "oidc_access_policies",
                "oidc_claim_mapping_sets",
                "oidc_role_claim_mappings",
                "oidc_group_claim_mappings",
            } <= set(inspector.get_table_names(schema=schema_name))
            assert "oidc_access_policy_generation" in {
                column["name"]
                for column in inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            assert "ck_oidc_providers_access_policy_generation" in _constraint_names(
                inspector, "oidc_providers", schema_name
            )
            assert {
                "role_sync_provenance",
                "role_sync_previous_role",
                "role_sync_applied_role",
                "role_sync_updated_at",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "external_identities", schema=schema_name
                )
            }
            assert "ck_external_identities_role_sync_provenance" in _constraint_names(
                inspector, "external_identities", schema_name
            )
            with schema_engine.connect() as connection:
                provenance = connection.execute(
                    text(
                        "SELECT role_sync_provenance, role_sync_applied_role, "
                        "role_sync_previous_role FROM external_identities WHERE id = :id"
                    ),
                    {"id": identity_id},
                ).one()
            assert tuple(provenance) == ("legacy", "admin", None)
            assert {
                "oidc_role_mapping_id",
                "oidc_assertion_expires_at",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "iam_user_role_assignments", schema=schema_name
                )
            }
            assert {"ck_iam_user_role_assignments_oidc_ownership"} <= _constraint_names(
                inspector, "iam_user_role_assignments", schema_name
            )
            assert {"fk_iam_user_role_assignments_oidc_mapping"} <= _foreign_key_names(
                inspector, "iam_user_role_assignments", schema_name
            )
            assert {
                "oidc_group_mapping_id",
                "oidc_assertion_expires_at",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "iam_group_memberships", schema=schema_name
                )
            }
            assert "ck_iam_group_memberships_oidc_ownership" in _constraint_names(
                inspector, "iam_group_memberships", schema_name
            )
            assert "fk_iam_group_memberships_oidc_mapping" in _foreign_key_names(
                inspector, "iam_group_memberships", schema_name
            )
            assert {"ck_oidc_access_policies_revision"} <= _constraint_names(
                inspector,
                "oidc_access_policies",
                schema_name,
            )
            assert {
                "ck_oidc_claim_mapping_sets_key",
                "ck_oidc_claim_mapping_sets_name",
                "ck_oidc_claim_mapping_sets_claim_path",
                "ck_oidc_claim_mapping_sets_missing_behavior",
                "ck_oidc_claim_mapping_sets_revision",
            } <= _constraint_names(
                inspector,
                "oidc_claim_mapping_sets",
                schema_name,
            )
            assert {
                "ck_oidc_role_claim_mappings_source_key",
                "ck_oidc_role_claim_mappings_claim_value",
                "ck_oidc_role_claim_mappings_custom_role",
            } <= _constraint_names(
                inspector,
                "oidc_role_claim_mappings",
                schema_name,
            )
            assert {
                "ck_oidc_group_claim_mappings_source_key",
                "ck_oidc_group_claim_mappings_claim_value",
                "ck_oidc_group_claim_mappings_custom_group",
            } <= _constraint_names(
                inspector,
                "oidc_group_claim_mappings",
                schema_name,
            )
            assert {
                "fk_oidc_role_claim_mappings_set",
                "fk_oidc_role_claim_mappings_custom_role",
            } <= _foreign_key_names(
                inspector,
                "oidc_role_claim_mappings",
                schema_name,
            )
            assert {
                "fk_oidc_group_claim_mappings_set",
                "fk_oidc_group_claim_mappings_custom_group",
            } <= _foreign_key_names(
                inspector,
                "oidc_group_claim_mappings",
                schema_name,
            )
            assert "uq_oidc_access_policies_provider" in _unique_names(
                inspector,
                "oidc_access_policies",
                schema_name,
            )
            assert {
                "uq_oidc_role_claim_mappings_source_key",
                "uq_oidc_role_claim_mappings_set_value",
                "uq_oidc_role_claim_mappings_grant_owner",
            } <= _unique_names(
                inspector,
                "oidc_role_claim_mappings",
                schema_name,
            )
            assert {
                "uq_oidc_group_claim_mappings_source_key",
                "uq_oidc_group_claim_mappings_set_value",
                "uq_oidc_group_claim_mappings_grant_owner",
            } <= _unique_names(
                inspector,
                "oidc_group_claim_mappings",
                schema_name,
            )
            assert {
                "ix_iam_user_role_assignments_oidc_mapping",
                "ix_iam_user_role_assignments_oidc_expiry",
            } <= _index_names(inspector, "iam_user_role_assignments", schema_name)
            assert {
                "ix_iam_group_memberships_oidc_mapping",
                "ix_iam_group_memberships_oidc_expiry",
            } <= _index_names(inspector, "iam_group_memberships", schema_name)
            assert {
                "ux_iam_roles_id_is_system_oidc",
            } <= _index_names(inspector, "iam_roles", schema_name)
            assert {
                "ux_iam_groups_id_is_system_oidc",
            } <= _index_names(inspector, "iam_groups", schema_name)

            with schema_engine.begin() as connection:
                collations = dict(
                    connection.execute(
                        text(
                            "SELECT table_name, collation_name "
                            "FROM information_schema.columns "
                            "WHERE table_schema = :schema_name "
                            "AND column_name = 'claim_value' "
                            "AND table_name IN "
                            "('oidc_role_claim_mappings', "
                            "'oidc_group_claim_mappings')"
                        ),
                        {"schema_name": schema_name},
                    ).all()
                )
                assert collations == {
                    "oidc_group_claim_mappings": "C",
                    "oidc_role_claim_mappings": "C",
                }
                connection.execute(
                    text(
                        "INSERT INTO oidc_access_policies "
                        "(id, provider_id, enabled, revision, updated_by_user_id) "
                        "VALUES (:id, :provider_id, true, 1, :user_id)"
                    ),
                    {
                        "id": policy_id,
                        "provider_id": provider_id,
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_claim_mapping_sets "
                        "(id, access_policy_id, key, name, claim_path, "
                        "missing_claim_behavior, enabled, revision, "
                        "updated_by_user_id) VALUES "
                        "(:id, :policy_id, 'threat-teams', 'Threat teams', "
                        "'realm_access.roles', 'deny', true, 1, :user_id)"
                    ),
                    {
                        "id": mapping_set_id,
                        "policy_id": policy_id,
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_role_claim_mappings "
                        "(id, mapping_set_id, source_key, claim_value, role_id) "
                        "VALUES (:id, :mapping_set_id, :source_key, "
                        "'Engineering', :role_id)"
                    ),
                    {
                        "id": role_mapping_id,
                        "mapping_set_id": mapping_set_id,
                        "source_key": f"oidc:role:{role_mapping_id.hex}",
                        "role_id": custom_role_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_role_claim_mappings "
                        "(id, mapping_set_id, source_key, claim_value, role_id) "
                        "VALUES (:id, :mapping_set_id, :source_key, "
                        "'engineering', :role_id)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "mapping_set_id": mapping_set_id,
                        "source_key": f"oidc:role:{uuid.uuid4().hex}",
                        "role_id": custom_role_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_group_claim_mappings "
                        "(id, mapping_set_id, source_key, claim_value, group_id) "
                        "VALUES (:id, :mapping_set_id, :source_key, "
                        "'Incident-Response', :group_id)"
                    ),
                    {
                        "id": group_mapping_id,
                        "mapping_set_id": mapping_set_id,
                        "source_key": f"oidc:group:{group_mapping_id.hex}",
                        "group_id": custom_group_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_user_role_assignments "
                        "(id, user_id, role_id, source, source_key, "
                        "oidc_role_mapping_id, oidc_assertion_expires_at) VALUES "
                        "(:id, :user_id, :role_id, 'oidc', :source_key, "
                        ":mapping_id, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                    ),
                    {
                        "id": mapped_role_assignment_id,
                        "user_id": user_id,
                        "role_id": custom_role_id,
                        "source_key": f"oidc:role:{role_mapping_id.hex}",
                        "mapping_id": role_mapping_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_group_memberships "
                        "(id, group_id, user_id, source, source_key, "
                        "oidc_group_mapping_id, oidc_assertion_expires_at) VALUES "
                        "(:id, :group_id, :user_id, 'oidc', :source_key, "
                        ":mapping_id, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                    ),
                    {
                        "id": mapped_group_membership_id,
                        "group_id": custom_group_id,
                        "user_id": user_id,
                        "source_key": f"oidc:group:{group_mapping_id.hex}",
                        "mapping_id": group_mapping_id,
                    },
                )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_access_policies (id, provider_id) "
                            "VALUES (:id, :provider_id)"
                        ),
                        {"id": uuid.uuid4(), "provider_id": provider_id},
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_claim_mapping_sets "
                            "(id, access_policy_id, key, name, claim_path, "
                            "missing_claim_behavior) VALUES "
                            "(:id, :policy_id, 'invalid-behavior', 'Invalid', "
                            "'groups', 'ignore')"
                        ),
                        {"id": uuid.uuid4(), "policy_id": policy_id},
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_role_claim_mappings "
                            "(id, mapping_set_id, source_key, claim_value, role_id) "
                            "VALUES (:id, :mapping_set_id, :source_key, "
                            "'Engineering', :role_id)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "mapping_set_id": mapping_set_id,
                            "source_key": f"oidc:role:{uuid.uuid4().hex}",
                            "role_id": custom_role_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_role_claim_mappings "
                            "(id, mapping_set_id, source_key, claim_value, role_id) "
                            "VALUES (:id, :mapping_set_id, :source_key, "
                            "'Administrators', :role_id)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "mapping_set_id": mapping_set_id,
                            "source_key": f"oidc:role:{uuid.uuid4().hex}",
                            "role_id": system_role_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_group_claim_mappings "
                            "(id, mapping_set_id, source_key, claim_value, group_id) "
                            "VALUES (:id, :mapping_set_id, :source_key, "
                            "'Everyone', :group_id)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "mapping_set_id": mapping_set_id,
                            "source_key": f"oidc:group:{uuid.uuid4().hex}",
                            "group_id": system_group_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO oidc_group_claim_mappings "
                            "(id, mapping_set_id, source_key, claim_value, group_id) "
                            "VALUES (:id, :mapping_set_id, 'invalid-source-key', "
                            "'Invalid source', :group_id)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "mapping_set_id": mapping_set_id,
                            "group_id": custom_group_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text("UPDATE iam_roles SET is_system = true WHERE id = :id"),
                        {"id": custom_role_id},
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text("UPDATE iam_groups SET is_system = true WHERE id = :id"),
                        {"id": custom_group_id},
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO iam_user_role_assignments "
                            "(id, user_id, role_id, source, source_key, "
                            "oidc_assertion_expires_at) VALUES "
                            "(:id, :user_id, :role_id, 'oidc', :source_key, "
                            "CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "user_id": user_id,
                            "role_id": custom_role_id,
                            "source_key": f"oidc:role:{uuid.uuid4().hex}",
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO iam_user_role_assignments "
                            "(id, user_id, role_id, source, source_key, "
                            "oidc_role_mapping_id, oidc_assertion_expires_at) VALUES "
                            "(:id, :user_id, :role_id, 'oidc', :source_key, "
                            ":mapping_id, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "user_id": user_id,
                            "role_id": custom_role_id,
                            "source_key": f"oidc:role:{uuid.uuid4().hex}",
                            "mapping_id": role_mapping_id,
                        },
                    )

            with pytest.raises(IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO iam_group_memberships "
                            "(id, group_id, user_id, source, source_key, "
                            "oidc_group_mapping_id, oidc_assertion_expires_at) VALUES "
                            "(:id, :group_id, :user_id, 'oidc', :source_key, "
                            ":mapping_id, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "group_id": system_group_id,
                            "user_id": user_id,
                            "source_key": f"oidc:group:{group_mapping_id.hex}",
                            "mapping_id": group_mapping_id,
                        },
                    )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM oidc_role_claim_mappings WHERE id = :id"),
                    {"id": role_mapping_id},
                )
                connection.execute(
                    text("DELETE FROM oidc_group_claim_mappings WHERE id = :id"),
                    {"id": group_mapping_id},
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM iam_user_role_assignments "
                            "WHERE id = :id"
                        ),
                        {"id": mapped_role_assignment_id},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM iam_group_memberships WHERE id = :id"
                        ),
                        {"id": mapped_group_membership_id},
                    )
                    == 0
                )

            with pytest.raises(RuntimeError, match="access policy state would be lost"):
                command.downgrade(config, "0064_service_accounts")

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM oidc_access_policies WHERE id = :id"),
                    {"id": policy_id},
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM oidc_claim_mapping_sets")
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM oidc_role_claim_mappings")
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM oidc_group_claim_mappings")
                    )
                    == 0
                )

            with pytest.raises(
                RuntimeError, match=r"external_identities\(role_sync_provenance\)"
            ):
                command.downgrade(config, "0064_service_accounts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE external_identities SET role_sync_provenance = NULL, "
                        "role_sync_previous_role = NULL, role_sync_applied_role = NULL, "
                        "role_sync_updated_at = NULL WHERE id = :id"
                    ),
                    {"id": identity_id},
                )

            command.downgrade(config, "0064_service_accounts")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            remaining_tables = set(inspector.get_table_names(schema=schema_name))
            assert "oidc_access_policies" not in remaining_tables
            assert "oidc_claim_mapping_sets" not in remaining_tables
            assert "oidc_role_claim_mappings" not in remaining_tables
            assert "oidc_group_claim_mappings" not in remaining_tables
            assert "ux_iam_roles_id_is_system_oidc" not in _index_names(
                inspector,
                "iam_roles",
                schema_name,
            )
            assert "ux_iam_groups_id_is_system_oidc" not in _index_names(
                inspector,
                "iam_groups",
                schema_name,
            )
            assert "oidc_access_policy_generation" not in {
                column["name"]
                for column in inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            assert "role_sync_provenance" not in {
                column["name"]
                for column in inspector.get_columns(
                    "external_identities", schema=schema_name
                )
            }
            assert "oidc_role_mapping_id" not in {
                column["name"]
                for column in inspector.get_columns(
                    "iam_user_role_assignments", schema=schema_name
                )
            }
            assert "oidc_group_mapping_id" not in {
                column["name"]
                for column in inspector.get_columns(
                    "iam_group_memberships", schema=schema_name
                )
            }
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM oidc_providers WHERE id = :id"),
                        {"id": provider_id},
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM iam_roles WHERE id = :id"),
                        {"id": custom_role_id},
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM iam_groups WHERE id = :id"),
                        {"id": custom_group_id},
                    )
                    == 1
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_oidc_claim_mapping_upgrade_refuses_unowned_legacy_grants(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0065_legacy_{uuid.uuid4().hex}"
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
            command.upgrade(config, "0064_service_accounts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_approved) VALUES "
                        "(:id, 'legacy-oidc-grant@example.com', 'hash', 'viewer', true)"
                    ),
                    {"id": user_id},
                )
                role_id = connection.scalar(
                    text("SELECT id FROM iam_roles ORDER BY key LIMIT 1")
                )
                group_id = connection.scalar(
                    text("SELECT id FROM iam_groups ORDER BY key LIMIT 1")
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_user_role_assignments "
                        "(id, user_id, role_id, source, source_key) VALUES "
                        "(:id, :user_id, :role_id, 'oidc', 'oidc:legacy:role')"
                    ),
                    {"id": uuid.uuid4(), "user_id": user_id, "role_id": role_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_user_role_assignments "
                        "(id, user_id, role_id, source, source_key) VALUES "
                        "(:id, :user_id, :role_id, 'oidc', 'oidc:legacy:role-2')"
                    ),
                    {"id": uuid.uuid4(), "user_id": user_id, "role_id": role_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_group_memberships "
                        "(id, group_id, user_id, source, source_key) VALUES "
                        "(:id, :group_id, :user_id, 'oidc', 'oidc:legacy:group')"
                    ),
                    {"id": uuid.uuid4(), "group_id": group_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO iam_group_memberships "
                        "(id, group_id, user_id, source, source_key) VALUES "
                        "(:id, :group_id, :user_id, 'oidc', 'oidc:legacy:group-2'), "
                        "(:local_id, :group_id, :user_id, 'local', '')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "local_id": uuid.uuid4(),
                        "group_id": group_id,
                        "user_id": user_id,
                    },
                )

            with pytest.raises(
                RuntimeError, match="unsupported preexisting OIDC IAM grants"
            ):
                command.upgrade(config, "0065_oidc_claim_mappings")

            inspector = inspect(schema_engine)
            assert "oidc_access_policies" not in set(
                inspector.get_table_names(schema=schema_name)
            )
            assert "oidc_access_policy_generation" not in {
                column["name"]
                for column in inspector.get_columns(
                    "oidc_providers", schema=schema_name
                )
            }
            with schema_engine.begin() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == ("0064_service_accounts")
                duplicate_roles, duplicate_groups = _convert_to_local(connection)
                assert duplicate_roles == 1
                assert duplicate_groups == 2
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM iam_user_role_assignments "
                            "WHERE user_id = :user_id AND role_id = :role_id "
                            "AND source = 'local' AND source_key = ''"
                        ),
                        {"user_id": user_id, "role_id": role_id},
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM iam_group_memberships "
                            "WHERE user_id = :user_id AND group_id = :group_id "
                            "AND source = 'local' AND source_key = ''"
                        ),
                        {"user_id": user_id, "group_id": group_id},
                    )
                    == 1
                )
            command.upgrade(config, "0065_oidc_claim_mappings")
            command.downgrade(config, "0064_service_accounts")
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_oidc_access_models_declare_migration_constraints_and_indexes():
    assert "ck_oidc_providers_access_policy_generation" in {
        item.name for item in OIDCProvider.__table__.constraints
    }
    assert "ck_external_identities_role_sync_provenance" in {
        item.name for item in ExternalIdentity.__table__.constraints
    }
    assert "ck_iam_user_role_assignments_oidc_ownership" in {
        item.name for item in IAMUserRoleAssignment.__table__.constraints
    }
    assert "ck_iam_group_memberships_oidc_ownership" in {
        item.name for item in IAMGroupMembership.__table__.constraints
    }
    assert {
        "ck_oidc_access_policies_revision",
        "uq_oidc_access_policies_provider",
    } <= {item.name for item in OIDCAccessPolicy.__table__.constraints}
    assert {
        "ck_oidc_claim_mapping_sets_key",
        "ck_oidc_claim_mapping_sets_name",
        "ck_oidc_claim_mapping_sets_claim_path",
        "ck_oidc_claim_mapping_sets_missing_behavior",
        "ck_oidc_claim_mapping_sets_revision",
        "uq_oidc_claim_mapping_sets_policy_key",
    } <= {item.name for item in OIDCClaimMappingSet.__table__.constraints}
    assert {
        "ck_oidc_role_claim_mappings_source_key",
        "ck_oidc_role_claim_mappings_claim_value",
        "ck_oidc_role_claim_mappings_custom_role",
        "fk_oidc_role_claim_mappings_custom_role",
        "uq_oidc_role_claim_mappings_source_key",
        "uq_oidc_role_claim_mappings_set_value",
        "uq_oidc_role_claim_mappings_grant_owner",
    } <= {item.name for item in OIDCRoleClaimMapping.__table__.constraints}
    assert {
        "ck_oidc_group_claim_mappings_source_key",
        "ck_oidc_group_claim_mappings_claim_value",
        "ck_oidc_group_claim_mappings_custom_group",
        "fk_oidc_group_claim_mappings_custom_group",
        "uq_oidc_group_claim_mappings_source_key",
        "uq_oidc_group_claim_mappings_set_value",
        "uq_oidc_group_claim_mappings_grant_owner",
    } <= {item.name for item in OIDCGroupClaimMapping.__table__.constraints}
    role_owner_fk = next(
        item
        for item in IAMUserRoleAssignment.__table__.foreign_key_constraints
        if item.name == "fk_iam_user_role_assignments_oidc_mapping"
    )
    assert [column.name for column in role_owner_fk.columns] == [
        "oidc_role_mapping_id",
        "source_key",
        "role_id",
    ]
    assert [element.column.name for element in role_owner_fk.elements] == [
        "id",
        "source_key",
        "role_id",
    ]
    assert role_owner_fk.ondelete == "CASCADE"
    group_owner_fk = next(
        item
        for item in IAMGroupMembership.__table__.foreign_key_constraints
        if item.name == "fk_iam_group_memberships_oidc_mapping"
    )
    assert [column.name for column in group_owner_fk.columns] == [
        "oidc_group_mapping_id",
        "source_key",
        "group_id",
    ]
    assert [element.column.name for element in group_owner_fk.elements] == [
        "id",
        "source_key",
        "group_id",
    ]
    assert group_owner_fk.ondelete == "CASCADE"


def test_oidc_access_admin_schemas_preserve_exact_values_and_forbid_unsafe_input():
    role_id = uuid.uuid4()
    request = OIDCClaimMappingSetCreateRequest(
        key="Threat-Teams",
        name=" Threat teams ",
        claim_path="realm_access.roles",
        missing_claim_behavior="deny",
        role_mappings=[
            OIDCRoleValueMappingRequest(
                claim_value="Engineering",
                role_id=role_id,
            ),
            OIDCRoleValueMappingRequest(
                claim_value="engineering",
                role_id=role_id,
            ),
        ],
        group_mappings=[
            OIDCGroupValueMappingRequest(
                claim_value="Incident-Response",
                group_id=uuid.uuid4(),
            )
        ],
    )
    assert request.key == "threat-teams"
    assert request.name == "Threat teams"
    assert [mapping.claim_value for mapping in request.role_mappings] == [
        "Engineering",
        "engineering",
    ]

    with pytest.raises(ValidationError, match="Exact role claim values must be unique"):
        OIDCClaimMappingSetCreateRequest(
            key="duplicate-values",
            name="Duplicate values",
            claim_path="groups",
            role_mappings=[
                {"claim_value": "Engineering", "role_id": role_id},
                {"claim_value": "Engineering", "role_id": uuid.uuid4()},
            ],
        )

    with pytest.raises(ValidationError, match="leading or trailing whitespace"):
        OIDCRoleValueMappingRequest(
            claim_value=" Engineering ",
            role_id=role_id,
        )

    with pytest.raises(ValidationError, match="Claim path must contain"):
        OIDCClaimMappingSetCreateRequest(
            key="unsafe-path",
            name="Unsafe path",
            claim_path="groups[0]",
        )

    with pytest.raises(ValidationError, match="At least one mapping-set field"):
        OIDCClaimMappingSetUpdateRequest(expected_revision=1)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OIDCClaimMappingSetCreateRequest(
            key="no-secrets",
            name="No secrets",
            claim_path="groups",
            client_secret="must-not-be-accepted",
        )

    assert not {
        "client_secret",
        "claims",
        "raw_claims",
    } & set(OIDCAccessPolicyResponse.model_fields)
