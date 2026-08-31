from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_INSERT_RECEIPT = text(
    "INSERT INTO ai_provider_attempt_receipts "
    "(id, operation_id, attempt_number, request_fingerprint, "
    "task_run_id_snapshot, feature_type, resource_type, resource_id, "
    "max_attempts, requested_max_tokens, reservation_generation, "
    "pre_io_failure_count, last_pre_io_failure_at, revision, iam_revision, "
    "data_policy_revision, data_policy_mode, state, io_outcome, retryable, "
    "next_max_tokens, "
    "settled_at, reconciliation_action, reconciled_from_state, "
    "reconciled_from_io_outcome, reconciled_by_user_id_snapshot, "
    "reconciled_at) VALUES "
    "(:id, :operation_id, :attempt_number, :request_fingerprint, "
    ":task_run_id_snapshot, :feature_type, :resource_type, :resource_id, "
    ":max_attempts, :requested_max_tokens, :reservation_generation, "
    ":pre_io_failure_count, :last_pre_io_failure_at, :revision, :iam_revision, "
    ":data_policy_revision, :data_policy_mode, :state, :io_outcome, "
    ":retryable, :next_max_tokens, :settled_at, :reconciliation_action, "
    ":reconciled_from_state, :reconciled_from_io_outcome, "
    ":reconciled_by_user_id_snapshot, :reconciled_at)"
)


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


def _receipt_values(**overrides) -> dict:
    values = {
        "id": uuid.uuid4(),
        "operation_id": uuid.uuid4(),
        "attempt_number": 1,
        "request_fingerprint": "a" * 64,
        "task_run_id_snapshot": uuid.uuid4(),
        "feature_type": "item_enrichment",
        "resource_type": "item",
        "resource_id": uuid.uuid4(),
        "max_attempts": 3,
        "requested_max_tokens": 1_024,
        "reservation_generation": 1,
        "pre_io_failure_count": 0,
        "last_pre_io_failure_at": None,
        "revision": 1,
        "iam_revision": 2,
        "data_policy_revision": 3,
        "data_policy_mode": "enforced",
        "state": "reserved",
        "io_outcome": "reserved",
        "retryable": None,
        "next_max_tokens": None,
        "settled_at": None,
        "reconciliation_action": None,
        "reconciled_from_state": None,
        "reconciled_from_io_outcome": None,
        "reconciled_by_user_id_snapshot": None,
        "reconciled_at": None,
    }
    values.update(overrides)
    return values


def _assert_constraint(
    engine: Engine,
    expected_constraint: str,
    **overrides,
) -> None:
    with pytest.raises(IntegrityError) as error:
        with engine.begin() as connection:
            connection.execute(_INSERT_RECEIPT, _receipt_values(**overrides))
    assert error.value.orig.diag.constraint_name == expected_constraint


def test_ai_provider_attempt_receipt_migration_contract_and_guarded_downgrade(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0074_{uuid.uuid4().hex}"
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
            command.upgrade(config, "0073_alert_metric_data_policy")
            command.upgrade(config, "0074_ai_provider_receipts")

            inspector = inspect(schema_engine)
            assert "ai_provider_attempt_receipts" in inspector.get_table_names(
                schema=schema_name
            )
            assert not inspector.get_foreign_keys(
                "ai_provider_attempt_receipts", schema=schema_name
            )
            assert {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "ai_provider_attempt_receipts", schema=schema_name
                )
            } == {
                "ck_ai_provider_attempt_receipts_attempt_bounds",
                "ck_ai_provider_attempt_receipts_token_bounds",
                "ck_ai_provider_attempt_receipts_iam_revision",
                "ck_ai_provider_attempt_receipts_revision",
                "ck_ai_provider_attempt_receipts_reservation_generation",
                "ck_ai_provider_attempt_receipts_pre_io_failures",
                "ck_ai_provider_attempt_receipts_policy_revision",
                "ck_ai_provider_attempt_receipts_fingerprint",
                "ck_ai_provider_attempt_receipts_policy_mode",
                "ck_ai_provider_attempt_receipts_state",
                "ck_ai_provider_attempt_receipts_io_outcome",
                "ck_ai_provider_attempt_receipts_lifecycle",
                "ck_ai_provider_attempt_receipts_reconciliation",
            }
            assert {
                constraint["name"]: tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(
                    "ai_provider_attempt_receipts", schema=schema_name
                )
            } == {
                "uq_ai_provider_attempt_receipts_operation_attempt": (
                    "operation_id",
                    "attempt_number",
                )
            }
            assert {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes(
                    "ai_provider_attempt_receipts", schema=schema_name
                )
                if not index.get("duplicates_constraint")
            } == {
                "ix_ai_provider_attempt_receipts_resource": (
                    "resource_type",
                    "resource_id",
                ),
                "ix_ai_provider_attempt_receipts_task_run_snapshot": (
                    "task_run_id_snapshot",
                ),
            }

            reserved = _receipt_values()
            with schema_engine.begin() as connection:
                connection.execute(_INSERT_RECEIPT, reserved)
                persisted = connection.execute(
                    text(
                        "SELECT state, io_outcome, retryable, next_max_tokens, "
                        "reservation_generation, pre_io_failure_count, "
                        "last_pre_io_failure_at, revision, "
                        "settled_at, created_at IS NOT NULL, updated_at IS NOT NULL "
                        "FROM ai_provider_attempt_receipts WHERE id = :id"
                    ),
                    {"id": reserved["id"]},
                ).one()
                assert persisted == (
                    "reserved",
                    "reserved",
                    None,
                    None,
                    1,
                    0,
                    None,
                    1,
                    None,
                    True,
                    True,
                )
                connection.execute(
                    text(
                        "UPDATE ai_provider_attempt_receipts SET "
                        "state = 'failed', io_outcome = 'not_sent', "
                        "retryable = true, next_max_tokens = requested_max_tokens, "
                        "settled_at = now() WHERE id = :id"
                    ),
                    {"id": reserved["id"]},
                )

            _assert_constraint(
                schema_engine,
                "uq_ai_provider_attempt_receipts_operation_attempt",
                operation_id=reserved["operation_id"],
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_attempt_bounds",
                attempt_number=4,
                max_attempts=3,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_fingerprint",
                request_fingerprint="A" * 64,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_policy_mode",
                data_policy_mode="observe",
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_iam_revision",
                iam_revision=0,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_revision",
                revision=0,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_reservation_generation",
                reservation_generation=0,
                state="voided",
                io_outcome="not_sent",
                retryable=True,
                settled_at="2026-08-31T00:00:00+00:00",
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_pre_io_failures",
                pre_io_failure_count=1,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_policy_revision",
                data_policy_revision=0,
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_lifecycle",
                state="succeeded",
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_token_bounds",
                state="failed",
                io_outcome="response_received",
                retryable=True,
                next_max_tokens=0,
                settled_at="2026-08-31T00:00:00+00:00",
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_reconciliation",
                state="ambiguous",
                io_outcome="ambiguous",
                retryable=False,
                settled_at="2026-08-31T00:00:00+00:00",
                reconciliation_action="acknowledged_may_have_sent",
                reconciled_from_state="reserved",
                reconciled_from_io_outcome="ambiguous",
                reconciled_by_user_id_snapshot=uuid.uuid4(),
                reconciled_at="2026-08-31T00:01:00+00:00",
            )
            _assert_constraint(
                schema_engine,
                "ck_ai_provider_attempt_receipts_reconciliation",
                attempt_number=1,
                max_attempts=1,
                state="failed",
                io_outcome="not_sent",
                retryable=True,
                next_max_tokens=1_024,
                settled_at="2026-08-31T00:00:00+00:00",
                reconciliation_action="confirmed_not_sent",
                reconciled_from_state="reserved",
                reconciled_from_io_outcome="reserved",
                reconciled_by_user_id_snapshot=uuid.uuid4(),
                reconciled_at="2026-08-31T00:00:00+00:00",
            )

            with schema_engine.begin() as connection:
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="voided",
                        io_outcome="not_sent",
                        retryable=True,
                        reservation_generation=1,
                        pre_io_failure_count=1,
                        last_pre_io_failure_at="2026-08-31T00:00:00+00:00",
                        settled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        reservation_generation=2,
                        pre_io_failure_count=1,
                        last_pre_io_failure_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="failed",
                        io_outcome="response_received",
                        retryable=True,
                        next_max_tokens=2_048,
                        settled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="failed",
                        io_outcome="not_sent",
                        retryable=True,
                        next_max_tokens=1_024,
                        settled_at="2026-08-31T00:00:00+00:00",
                        reconciliation_action="confirmed_not_sent",
                        reconciled_from_state="reserved",
                        reconciled_from_io_outcome="reserved",
                        reconciled_by_user_id_snapshot=uuid.uuid4(),
                        reconciled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        attempt_number=1,
                        max_attempts=1,
                        state="failed",
                        io_outcome="not_sent",
                        retryable=False,
                        settled_at="2026-08-31T00:00:00+00:00",
                        reconciliation_action="confirmed_not_sent",
                        reconciled_from_state="reserved",
                        reconciled_from_io_outcome="reserved",
                        reconciled_by_user_id_snapshot=uuid.uuid4(),
                        reconciled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="ambiguous",
                        io_outcome="ambiguous",
                        retryable=False,
                        settled_at="2026-08-31T00:00:00+00:00",
                        reconciliation_action="acknowledged_may_have_sent",
                        reconciled_from_state="ambiguous",
                        reconciled_from_io_outcome="ambiguous",
                        reconciled_by_user_id_snapshot=uuid.uuid4(),
                        reconciled_at="2026-08-31T00:01:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="succeeded",
                        io_outcome="response_received",
                        retryable=False,
                        settled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    _INSERT_RECEIPT,
                    _receipt_values(
                        state="ambiguous",
                        io_outcome="ambiguous",
                        retryable=False,
                        settled_at="2026-08-31T00:00:00+00:00",
                    ),
                )
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0073_alert_metric_data_policy")
            with schema_engine.begin() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0074_ai_provider_receipts"
                )
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0073_alert_metric_data_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="durable provider history exists"):
                command.downgrade(config, "0073_alert_metric_data_policy")
            with schema_engine.begin() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "0074_ai_provider_receipts"
                )
                connection.execute(text("DELETE FROM ai_provider_attempt_receipts"))

            command.downgrade(config, "0073_alert_metric_data_policy")
            inspector.clear_cache()
            assert "ai_provider_attempt_receipts" not in inspector.get_table_names(
                schema=schema_name
            )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
