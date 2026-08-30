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


def _constraint_names(inspector, table_name: str, schema_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            table_name,
            schema=schema_name,
        )
    }


def _index_columns(inspector, table_name: str, schema_name: str) -> dict[str, tuple]:
    return {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes(table_name, schema=schema_name)
    }


def _insert_pending_approval(
    connection,
    *,
    approval_id: uuid.UUID,
    requester_id: uuid.UUID,
    digest: str = "a" * 64,
    lifetime_sql: str = "interval '1 hour'",
    target_id: uuid.UUID | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO action_approval_requests "
            "(id, action_type, action_label_snapshot, audit_action_snapshot, "
            "requester_permission_snapshot, approver_permission_snapshot, "
            "action_definition_version, target_type, target_id, target_revision, "
            "target_snapshot, payload_json, payload_digest, requested_by_user_id, "
            "requested_by_email_snapshot, request_reason, expires_at) VALUES "
            "(:id, 'service_account.disable', 'Disable service account', "
            "'service_accounts.disable', 'read:service_accounts', "
            "'write:service_accounts', 1, 'service_account', :target_id, 1, "
            "jsonb_build_object("
            "'precondition_digest', CAST(:precondition_digest AS text)), "
            "'{}'::jsonb, :digest, :requester_id, 'requester@example.test', "
            "'Disable a compromised automation account', "
            f"now() + {lifetime_sql})"
        ),
        {
            "id": approval_id,
            "target_id": str(target_id or uuid.uuid4()),
            "precondition_digest": "c" * 64,
            "digest": digest,
            "requester_id": requester_id,
        },
    )


def _assert_integrity_constraint(
    engine,
    expected_constraint: str,
    statement,
    parameters: dict | None = None,
) -> None:
    with pytest.raises(IntegrityError) as error:
        with engine.begin() as connection:
            connection.execute(statement, parameters or {})
    assert error.value.orig.diag.constraint_name == expected_constraint


def test_action_approval_migration_constraints_and_guarded_downgrade(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0067_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    requester_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    approval_target_id = uuid.uuid4()
    execution_receipt_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    error_operation_receipt_id = uuid.uuid4()

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
            command.upgrade(config, "0066_temporary_elevations")

            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0066_temporary_elevations"
                )

            command.upgrade(config, "0067_action_approvals")

            inspector = inspect(schema_engine)
            assert {
                "action_approval_requests",
                "action_execution_receipts",
            } <= set(inspector.get_table_names(schema=schema_name))
            assert {
                "requester_permission_snapshot",
                "approver_permission_snapshot",
                "action_definition_version",
                "target_snapshot",
                "payload_json",
                "payload_digest",
                "decided_auth_token_version_snapshot",
                "decided_auth_method_snapshot",
                "cancelled_from_status",
                "invalidation_reason",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "action_approval_requests",
                    schema=schema_name,
                )
            }
            assert {
                "approval_request_id",
                "payload_digest",
                "requester_email_snapshot",
                "approver_email_snapshot",
                "executed_by_email_snapshot",
                "result_json",
                "result_schema_version",
            } <= {
                column["name"]
                for column in inspector.get_columns(
                    "action_execution_receipts",
                    schema=schema_name,
                )
            }
            assert {
                "authorization_approval_id",
                "execution_receipt_id",
            } <= {
                column["name"]
                for column in inspector.get_columns("audit_logs", schema=schema_name)
            }
            approval_indexes = _index_columns(
                inspector,
                "action_approval_requests",
                schema_name,
            )
            assert approval_indexes["ix_action_approval_requests_status_expiry"] == (
                "status",
                "expires_at",
            )
            assert approval_indexes["ix_action_approval_requests_action_created"] == (
                "action_type",
                "created_at",
            )
            assert approval_indexes["ix_action_approval_requests_requester"] == (
                "requested_by_user_id",
            )
            assert approval_indexes["ix_action_approval_requests_decider"] == (
                "decided_by_user_id",
            )
            assert _index_columns(
                inspector,
                "action_execution_receipts",
                schema_name,
            )["ix_action_execution_receipts_created"] == ("created_at",)
            audit_indexes = _index_columns(inspector, "audit_logs", schema_name)
            assert audit_indexes["ix_audit_logs_authorization_approval"] == (
                "authorization_approval_id",
                "created_at",
            )
            assert audit_indexes["ix_audit_logs_execution_receipt"] == (
                "execution_receipt_id",
                "created_at",
            )
            assert {
                "ck_action_approval_requests_state",
                "ck_action_approval_requests_no_self_decision",
                "ck_action_approval_requests_expiry",
                "ck_action_approval_requests_payload_digest",
            } <= _constraint_names(
                inspector,
                "action_approval_requests",
                schema_name,
            )
            assert "ck_action_execution_receipts_payload_digest" in _constraint_names(
                inspector,
                "action_execution_receipts",
                schema_name,
            )

            with schema_engine.begin() as connection:
                for user_id, email in (
                    (requester_id, "requester@example.test"),
                    (approver_id, "approver@example.test"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO users "
                            "(id, email, password_hash, role, is_approved) "
                            "VALUES (:id, :email, 'hash', 'admin', true)"
                        ),
                        {"id": user_id, "email": email},
                    )
                _insert_pending_approval(
                    connection,
                    approval_id=approval_id,
                    requester_id=requester_id,
                    target_id=approval_target_id,
                )

            _assert_integrity_constraint(
                schema_engine,
                "ck_action_approval_requests_state",
                text(
                    "UPDATE action_approval_requests SET status = 'approved' "
                    "WHERE id = :id"
                ),
                {"id": approval_id},
            )
            _assert_integrity_constraint(
                schema_engine,
                "ck_action_approval_requests_no_self_decision",
                text(
                    "UPDATE action_approval_requests SET status = 'approved', "
                    "decided_by_user_id = :requester_id, "
                    "decided_by_email_snapshot = 'requester@example.test', "
                    "decided_at = now(), decision_reason = 'Approved for response', "
                    "decided_auth_token_version_snapshot = 0, "
                    "decided_auth_method_snapshot = 'local' WHERE id = :id"
                ),
                {"id": approval_id, "requester_id": requester_id},
            )

            for lifetime_sql in ("interval '4 minutes'", "interval '1 day 1 second'"):
                invalid_approval_id = uuid.uuid4()
                with pytest.raises(IntegrityError) as lifetime_error:
                    with schema_engine.begin() as connection:
                        _insert_pending_approval(
                            connection,
                            approval_id=invalid_approval_id,
                            requester_id=requester_id,
                            lifetime_sql=lifetime_sql,
                        )
                assert (
                    lifetime_error.value.orig.diag.constraint_name
                    == "ck_action_approval_requests_expiry"
                )

            with pytest.raises(IntegrityError) as digest_error:
                with schema_engine.begin() as connection:
                    _insert_pending_approval(
                        connection,
                        approval_id=uuid.uuid4(),
                        requester_id=requester_id,
                        digest="A" * 64,
                    )
            assert (
                digest_error.value.orig.diag.constraint_name
                == "ck_action_approval_requests_payload_digest"
            )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE action_approval_requests SET status = 'executed', "
                        "decided_by_user_id = :approver_id, "
                        "decided_by_email_snapshot = 'approver@example.test', "
                        "decided_at = now(), decision_reason = 'Approved for response', "
                        "decided_auth_token_version_snapshot = 0, "
                        "decided_auth_method_snapshot = 'local', "
                        "executed_by_user_id = :requester_id, "
                        "executed_by_email_snapshot = 'requester@example.test', "
                        "executed_at = now(), revision = revision + 1 WHERE id = :id"
                    ),
                    {
                        "id": approval_id,
                        "requester_id": requester_id,
                        "approver_id": approver_id,
                    },
                )

            invalid_receipt_statement = text(
                "INSERT INTO action_execution_receipts "
                "(id, approval_request_id, action_type, target_type, target_id, "
                "target_revision, payload_digest, requester_user_id, "
                "requester_email_snapshot, approver_user_id, "
                "approver_email_snapshot, executed_by_user_id, "
                "executed_by_email_snapshot, result_json) VALUES "
                "(:id, :approval_id, 'service_account.disable', 'service_account', "
                ":target_id, 1, :digest, :requester_id, 'requester@example.test', "
                ":approver_id, 'approver@example.test', :requester_id, "
                "'requester@example.test', '{}'::jsonb)"
            )
            receipt_parameters = {
                "id": execution_receipt_id,
                "approval_id": approval_id,
                "target_id": str(uuid.uuid4()),
                "digest": "A" * 64,
                "requester_id": requester_id,
                "approver_id": approver_id,
            }
            _assert_integrity_constraint(
                schema_engine,
                "ck_action_execution_receipts_payload_digest",
                invalid_receipt_statement,
                receipt_parameters,
            )

            receipt_parameters["digest"] = "a" * 64
            _assert_integrity_constraint(
                schema_engine,
                "ck_action_execution_receipts_matches_approval",
                invalid_receipt_statement,
                receipt_parameters,
            )

            receipt_parameters["target_id"] = str(approval_target_id)
            with schema_engine.begin() as connection:
                connection.execute(invalid_receipt_statement, receipt_parameters)

            _assert_integrity_constraint(
                schema_engine,
                "ck_action_execution_receipts_immutable",
                text(
                    "UPDATE action_execution_receipts SET result_json = "
                    "'{\"changed\": true}'::jsonb WHERE id = :id"
                ),
                {"id": execution_receipt_id},
            )

            duplicate_receipt_parameters = {
                **receipt_parameters,
                "id": uuid.uuid4(),
            }
            _assert_integrity_constraint(
                schema_engine,
                "uq_action_execution_receipts_approval_request",
                invalid_receipt_statement,
                duplicate_receipt_parameters,
            )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, actor_user_id, action, resource_type, resource_id, "
                        "success, metadata_json, authorization_approval_id, "
                        "execution_receipt_id) VALUES "
                        "(:id, :actor_id, 'approvals.action.execute', "
                        "'action_approval', :resource_id, true, '{}'::jsonb, "
                        ":approval_id, :receipt_id)"
                    ),
                    {
                        "id": audit_id,
                        "actor_id": requester_id,
                        "resource_id": str(approval_id),
                        "approval_id": approval_id,
                        "receipt_id": execution_receipt_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO governance_operation_receipts "
                        "(id, actor_user_id, operation, key_hash, request_fingerprint, "
                        "resource_type, resource_id, response_json, http_status) VALUES "
                        "(:id, :actor_id, 'approval.execute', :key_hash, :fingerprint, "
                        "'action_approval', :resource_id, "
                        '\'{"detail": "target changed"}\'::jsonb, 409)'
                    ),
                    {
                        "id": error_operation_receipt_id,
                        "actor_id": requester_id,
                        "key_hash": "d" * 64,
                        "fingerprint": "e" * 64,
                        "resource_id": approval_id,
                    },
                )
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT http_status FROM governance_operation_receipts "
                            "WHERE id = :id"
                        ),
                        {"id": error_operation_receipt_id},
                    )
                    == 409
                )

            with pytest.raises(
                RuntimeError,
                match="governance history would be lost",
            ) as downgrade_error:
                command.downgrade(config, "0066_temporary_elevations")
            downgrade_message = str(downgrade_error.value)
            assert "requests=1" in downgrade_message
            assert "receipts=1" in downgrade_message
            assert "audit_rows=1" in downgrade_message
            assert "error_operation_receipts=1" in downgrade_message

            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"),
                    {"id": audit_id},
                )
                connection.execute(text("DELETE FROM action_execution_receipts"))
                connection.execute(text("DELETE FROM action_approval_requests"))
                connection.execute(
                    text("DELETE FROM governance_operation_receipts WHERE id = :id"),
                    {"id": error_operation_receipt_id},
                )

            command.downgrade(config, "0066_temporary_elevations")
            inspector = inspect(schema_engine)
            inspector.clear_cache()
            assert "action_approval_requests" not in inspector.get_table_names(
                schema=schema_name
            )
            assert "action_execution_receipts" not in inspector.get_table_names(
                schema=schema_name
            )
            assert {
                "authorization_approval_id",
                "execution_receipt_id",
            }.isdisjoint(
                {
                    column["name"]
                    for column in inspector.get_columns(
                        "audit_logs",
                        schema=schema_name,
                    )
                }
            )
            downgraded_audit_indexes = _index_columns(
                inspector,
                "audit_logs",
                schema_name,
            )
            assert (
                "ix_audit_logs_authorization_approval" not in downgraded_audit_indexes
            )
            assert "ix_audit_logs_execution_receipt" not in downgraded_audit_indexes

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO governance_operation_receipts "
                        "(id, actor_user_id, operation, key_hash, request_fingerprint, "
                        "resource_type, resource_id, response_json, http_status) VALUES "
                        "(:id, :actor_id, 'approval.legacy.success', :key_hash, "
                        ":fingerprint, 'temporary_elevation', :resource_id, "
                        "'{}'::jsonb, 299)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "actor_id": requester_id,
                        "key_hash": "f" * 64,
                        "fingerprint": "0" * 64,
                        "resource_id": uuid.uuid4(),
                    },
                )

            _assert_integrity_constraint(
                schema_engine,
                "ck_governance_operation_receipts_http_status",
                text(
                    "INSERT INTO governance_operation_receipts "
                    "(id, actor_user_id, operation, key_hash, request_fingerprint, "
                    "resource_type, resource_id, response_json, http_status) VALUES "
                    "(:id, :actor_id, 'approval.legacy.conflict', :key_hash, "
                    ":fingerprint, 'temporary_elevation', :resource_id, "
                    "'{}'::jsonb, 409)"
                ),
                {
                    "id": uuid.uuid4(),
                    "actor_id": requester_id,
                    "key_hash": "1" * 64,
                    "fingerprint": "2" * 64,
                    "resource_id": uuid.uuid4(),
                },
            )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
