from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

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


def _table_exists(engine, schema_name: str, table_name: str) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :schema_name AND table_name = :table_name)"
                ),
                {"schema_name": schema_name, "table_name": table_name},
            )
        )


def _column_exists(engine, schema_name: str, table_name: str, column_name: str) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = :schema_name AND table_name = :table_name "
                    "AND column_name = :column_name)"
                ),
                {
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "column_name": column_name,
                },
            )
        )


def _index_definition(engine, schema_name: str, index_name: str) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = :schema_name AND indexname = :index_name"
            ),
            {"schema_name": schema_name, "index_name": index_name},
        )


def test_lifecycle_migration_upgrades_and_downgrades(test_database_url, monkeypatch):
    schema_name = f"migration_0085_{uuid.uuid4().hex}"
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
            command.upgrade(config, "0084_ioc_candidate_search")
            assert not _table_exists(schema_engine, schema_name, "lifecycle_policies")

            command.upgrade(config, "0085_lifecycle_management")
            for table_name in (
                "lifecycle_catalog_state",
                "lifecycle_policies",
                "lifecycle_previews",
                "lifecycle_runs",
            ):
                assert _table_exists(schema_engine, schema_name, table_name)
            assert _column_exists(
                schema_engine,
                schema_name,
                "articles",
                "content_purged_at",
            )
            assert _column_exists(
                schema_engine,
                schema_name,
                "articles",
                "content_purge_run_id",
            )
            assert _column_exists(
                schema_engine,
                schema_name,
                "lifecycle_policies",
                "configuration_updated_at",
            )
            assert _column_exists(
                schema_engine,
                schema_name,
                "lifecycle_policies",
                "updated_by_label_snapshot",
            )
            active_index = _index_definition(
                schema_engine,
                schema_name,
                "uq_lifecycle_runs_active_target",
            )
            article_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_articles_retention_candidates",
            )
            lifecycle_age_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_items_lifecycle_age",
            )
            integration_run_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_integration_runs_lifecycle_finished",
            )
            integration_event_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_integration_events_lifecycle_routed",
            )
            alert_evaluation_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_alert_evaluation_requests_lifecycle_terminal",
            )
            integration_delivery_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_integration_deliveries_lifecycle_terminal",
            )
            auth_session_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_auth_sessions_lifecycle_terminal",
            )
            provider_receipt_index = _index_definition(
                schema_engine,
                schema_name,
                "ix_ai_provider_attempt_receipts_lifecycle_updated",
            )
            assert active_index is not None
            assert "UNIQUE INDEX" in active_index
            assert "status" in active_index
            assert article_index is not None
            assert "content_purged_at IS NULL" in article_index
            assert "item_id" in article_index
            assert "title_extracted IS NOT NULL" in article_index
            assert lifecycle_age_index is not None
            assert "COALESCE(published_at, first_seen_at)" in lifecycle_age_index
            assert lifecycle_age_index.endswith(", id)")
            assert integration_run_index is not None
            assert "(finished_at, id) WHERE (finished_at IS NOT NULL)" in (
                integration_run_index
            )
            assert integration_event_index is not None
            assert "(created_at, id)" in integration_event_index
            assert "routing_state" in integration_event_index
            assert "dead_letter" in integration_event_index
            assert alert_evaluation_index is not None
            assert "(completed_at, id)" in alert_evaluation_index
            assert "state" in alert_evaluation_index
            assert "dead_letter" in alert_evaluation_index
            assert "completed_at IS NOT NULL" in alert_evaluation_index
            assert integration_delivery_index is not None
            assert (
                "COALESCE(completed_at, dead_lettered_at, updated_at)"
                in integration_delivery_index
            )
            assert "metrics_aggregated_at IS NOT NULL" in integration_delivery_index
            assert auth_session_index is not None
            assert (
                "COALESCE(revoked_at, LEAST(idle_expires_at, absolute_expires_at))"
                in auth_session_index
            )
            assert provider_receipt_index is not None
            assert "(updated_at, operation_id, id)" in provider_receipt_index
            with schema_engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM lifecycle_policies")) == 0
                assert connection.scalar(
                    text("SELECT count(*) FROM lifecycle_catalog_state")
                ) == 1

            actor_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_active, is_approved, "
                        "auth_token_version, password_login_enabled, provisioning_source) "
                        "VALUES (:actor_id, 'lifecycle-owner@example.test', "
                        "'not-used', 'admin', true, true, 0, true, 'local')"
                    ),
                    {"actor_id": actor_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO lifecycle_policies "
                        "(target_key, enabled, retention_days, schedule_cadence, "
                        "schedule_hour_utc, max_records_per_run, options_json, "
                        "revision, next_run_at) VALUES "
                        "('audit_logs', false, 730, 'daily', 2, 100, '{}', 1, NULL)"
                    )
                )
            with pytest.raises(DBAPIError, match="must advance revision"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_policies SET retention_days = 729 "
                            "WHERE target_key = 'audit_logs'"
                        )
                    )
            with pytest.raises(DBAPIError, match="configuration-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_policies "
                            "SET updated_by_label_snapshot = 'False attribution' "
                            "WHERE target_key = 'audit_logs'"
                        )
                    )
            with pytest.raises(DBAPIError, match="target identity is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_policies "
                            "SET target_key = 'ai_usage_history' "
                            "WHERE target_key = 'audit_logs'"
                        )
                    )
            with schema_engine.begin() as connection:
                initial_configuration_at = connection.scalar(
                    text(
                        "SELECT configuration_updated_at FROM lifecycle_policies "
                        "WHERE target_key = 'audit_logs'"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE lifecycle_policies SET last_run_at = CURRENT_TIMESTAMP, "
                        "last_run_status = 'succeeded' WHERE target_key = 'audit_logs'"
                    )
                )
                assert connection.scalar(
                    text(
                        "SELECT configuration_updated_at FROM lifecycle_policies "
                        "WHERE target_key = 'audit_logs'"
                    )
                ) == initial_configuration_at
                connection.execute(
                    text(
                        "UPDATE lifecycle_policies SET retention_days = 729, revision = 2, "
                        "updated_by_user_id = :actor_id, "
                        "updated_by_label_snapshot = 'lifecycle-owner@example.test' "
                        "WHERE target_key = 'audit_logs'"
                    ),
                    {"actor_id": actor_id},
                )
                changed_configuration_at = connection.scalar(
                    text(
                        "SELECT configuration_updated_at FROM lifecycle_policies "
                        "WHERE target_key = 'audit_logs'"
                    )
                )
                assert changed_configuration_at > initial_configuration_at
                connection.execute(
                    text("DELETE FROM users WHERE id = :actor_id"),
                    {"actor_id": actor_id},
                )
                attribution = connection.execute(
                    text(
                        "SELECT updated_by_user_id, updated_by_label_snapshot "
                        "FROM lifecycle_policies WHERE target_key = 'audit_logs'"
                    )
                ).one()
                assert attribution == (None, "lifecycle-owner@example.test")

            run_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO lifecycle_runs "
                        "(id, target_key, trigger_source, status, policy_revision, "
                        "policy_snapshot_json, cutoff_at, scheduled_for, max_records, "
                        "queued_at, finished_at, stop_reason) VALUES "
                        "(:run_id, 'audit_logs', 'scheduled', 'succeeded', 2, '{}', "
                        "CURRENT_TIMESTAMP - INTERVAL '2 days', "
                        "CURRENT_TIMESTAMP - INTERVAL '1 day', 100, "
                        "CURRENT_TIMESTAMP - INTERVAL '1 day', CURRENT_TIMESTAMP, "
                        "'completed')"
                    ),
                    {"run_id": run_id},
                )
            with pytest.raises(DBAPIError, match="terminal lifecycle run evidence"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs SET status = 'queued', "
                            "finished_at = NULL, stop_reason = NULL WHERE id = :run_id"
                        ),
                        {"run_id": run_id},
                    )
            with pytest.raises(DBAPIError, match="terminal lifecycle run evidence"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs SET error_message = 'changed' "
                            "WHERE id = :run_id"
                        ),
                        {"run_id": run_id},
                    )
            with schema_engine.connect() as connection:
                terminal_evidence = connection.execute(
                    text(
                        "SELECT status, stop_reason, error_message FROM lifecycle_runs "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": run_id},
                ).one()
                assert terminal_evidence == ("succeeded", "completed", None)

            cancelled_run_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO lifecycle_runs "
                        "(id, target_key, trigger_source, status, policy_revision, "
                        "policy_snapshot_json, cutoff_at, scheduled_for, max_records, "
                        "queued_at, finished_at, stop_reason) VALUES "
                        "(:run_id, 'audit_logs', 'scheduled', 'cancelled', 2, '{}', "
                        "CURRENT_TIMESTAMP - INTERVAL '4 days', "
                        "CURRENT_TIMESTAMP - INTERVAL '3 days', 100, "
                        "CURRENT_TIMESTAMP - INTERVAL '3 days', "
                        "CURRENT_TIMESTAMP - INTERVAL '2 days', 'policy_changed')"
                    ),
                    {"run_id": cancelled_run_id},
                )
            with pytest.raises(DBAPIError, match="terminal lifecycle run evidence"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs SET cancel_requested = true, "
                            "cancel_requested_at = CURRENT_TIMESTAMP, "
                            "cancel_requested_by_principal_type = 'system', "
                            "cancellation_reason = 'Injected cancellation evidence' "
                            "WHERE id = :run_id"
                        ),
                        {"run_id": cancelled_run_id},
                    )

            requester_id = uuid.uuid4()
            canceller_id = uuid.uuid4()
            preview_id = uuid.uuid4()
            alternate_preview_id = uuid.uuid4()
            manual_run_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, role, is_active, is_approved, "
                        "auth_token_version, password_login_enabled, provisioning_source) "
                        "VALUES "
                        "(:requester_id, 'requester@example.test', 'not-used', "
                        "'admin', true, true, 0, true, 'local'), "
                        "(:canceller_id, 'canceller@example.test', 'not-used', "
                        "'admin', true, true, 0, true, 'local')"
                    ),
                    {
                        "requester_id": requester_id,
                        "canceller_id": canceller_id,
                    },
                )
                for current_preview_id, fingerprint in (
                    (preview_id, "c" * 64),
                    (alternate_preview_id, "d" * 64),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO lifecycle_previews "
                            "(id, target_key, policy_revision, policy_snapshot_json, "
                            "request_fingerprint, cutoff_at, requested_by_user_id, "
                            "expires_at) VALUES (:preview_id, 'audit_logs', 2, '{}', "
                            ":fingerprint, CURRENT_TIMESTAMP - INTERVAL '2 days', "
                            ":requester_id, CURRENT_TIMESTAMP + INTERVAL '1 hour')"
                        ),
                        {
                            "preview_id": current_preview_id,
                            "fingerprint": fingerprint,
                            "requester_id": requester_id,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO lifecycle_runs "
                        "(id, target_key, trigger_source, status, policy_revision, "
                        "policy_snapshot_json, preview_id, preview_id_snapshot, "
                        "requested_by_user_id, requested_by_user_id_snapshot, "
                        "requested_by_label_snapshot, reason, idempotency_key_hash, "
                        "request_fingerprint, cutoff_at, max_records, queued_at) VALUES "
                        "(:run_id, 'audit_logs', 'manual', 'queued', 2, '{}', "
                        ":preview_id, :preview_id, :requester_id, :requester_id, "
                        "'requester@example.test', 'Approved cleanup request', "
                        ":idempotency_hash, :request_hash, "
                        "CURRENT_TIMESTAMP - INTERVAL '2 days', 100, "
                        "CURRENT_TIMESTAMP - INTERVAL '1 day')"
                    ),
                    {
                        "run_id": manual_run_id,
                        "preview_id": preview_id,
                        "requester_id": requester_id,
                        "idempotency_hash": "a" * 64,
                        "request_hash": "b" * 64,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE lifecycle_runs SET cancel_requested = true, "
                        "cancel_requested_at = CURRENT_TIMESTAMP, "
                        "cancel_requested_by_user_id = :canceller_id, "
                        "cancel_requested_by_principal_type = 'user', "
                        "cancel_requested_by_user_id_snapshot = :canceller_id, "
                        "cancel_requested_by_label_snapshot = 'canceller@example.test', "
                        "cancellation_reason = 'Approved cancellation request' "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": manual_run_id, "canceller_id": canceller_id},
                )
            with pytest.raises(DBAPIError, match="preview evidence is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_previews "
                            "SET cutoff_at = cutoff_at - INTERVAL '1 day' "
                            "WHERE id = :preview_id"
                        ),
                        {"preview_id": preview_id},
                    )
            with pytest.raises(DBAPIError, match="preview evidence is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_previews "
                            "SET requested_by_user_id = :canceller_id "
                            "WHERE id = :preview_id"
                        ),
                        {
                            "preview_id": preview_id,
                            "canceller_id": canceller_id,
                        },
                    )
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE lifecycle_previews SET used_at = CURRENT_TIMESTAMP "
                        "WHERE id = :preview_id"
                    ),
                    {"preview_id": preview_id},
                )
                assert connection.scalar(
                    text(
                        "SELECT used_at IS NOT NULL FROM lifecycle_previews "
                        "WHERE id = :preview_id"
                    ),
                    {"preview_id": preview_id},
                )
            with pytest.raises(
                DBAPIError,
                match="preview consumption is immutable",
            ):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_previews "
                            "SET used_at = used_at + INTERVAL '1 second' "
                            "WHERE id = :preview_id"
                        ),
                        {"preview_id": preview_id},
                    )
            with pytest.raises(DBAPIError, match="request evidence is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs SET preview_id = :preview_id "
                            "WHERE id = :run_id"
                        ),
                        {
                            "run_id": manual_run_id,
                            "preview_id": alternate_preview_id,
                        },
                    )
            with pytest.raises(DBAPIError, match="request evidence is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs "
                            "SET requested_by_user_id = :canceller_id "
                            "WHERE id = :run_id"
                        ),
                        {"run_id": manual_run_id, "canceller_id": canceller_id},
                    )
            with pytest.raises(DBAPIError, match="cancellation evidence is immutable"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE lifecycle_runs "
                            "SET cancel_requested_by_user_id = :requester_id "
                            "WHERE id = :run_id"
                        ),
                        {"run_id": manual_run_id, "requester_id": requester_id},
                    )
            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM lifecycle_previews WHERE id = :preview_id"),
                    {"preview_id": preview_id},
                )
                connection.execute(
                    text("DELETE FROM users WHERE id IN (:requester_id, :canceller_id)"),
                    {
                        "requester_id": requester_id,
                        "canceller_id": canceller_id,
                    },
                )
                live_links = connection.execute(
                    text(
                        "SELECT preview_id, requested_by_user_id, "
                        "cancel_requested_by_user_id, requested_by_user_id_snapshot, "
                        "cancel_requested_by_user_id_snapshot FROM lifecycle_runs "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": manual_run_id},
                ).one()
                assert live_links == (
                    None,
                    None,
                    None,
                    requester_id,
                    canceller_id,
                )
            command.check(config)

            command.downgrade(config, "0084_ioc_candidate_search")
            assert not _table_exists(schema_engine, schema_name, "lifecycle_runs")
            assert not _table_exists(schema_engine, schema_name, "lifecycle_previews")
            assert not _table_exists(schema_engine, schema_name, "lifecycle_policies")
            assert not _column_exists(
                schema_engine,
                schema_name,
                "articles",
                "content_purged_at",
            )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
