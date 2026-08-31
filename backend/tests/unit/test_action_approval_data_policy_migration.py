from __future__ import annotations

import uuid
from pathlib import Path
from queue import Queue
from threading import Thread
from time import monotonic, sleep

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID


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


def test_action_approval_migration_backfills_quarantines_fences_and_round_trips(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0080_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    restricted_label_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    run_id = uuid.uuid4()
    system_run_id = uuid.uuid4()
    other_run_id = uuid.uuid4()
    run_envelope_id = uuid.uuid4()
    run_source_id = uuid.uuid4()
    other_run_envelope_id = uuid.uuid4()
    other_run_source_id = uuid.uuid4()
    receipt_id = uuid.uuid4()
    system_approval_id = uuid.uuid4()
    governed_approval_id = uuid.uuid4()
    missing_approval_id = uuid.uuid4()
    unknown_approval_id = uuid.uuid4()

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
            command.upgrade(config, "0079_metric_captured_taint")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO handling_labels "
                        "(id, key, name, color) VALUES "
                        "(:id, 'approval-migration-restricted', "
                        "'Approval migration restricted', '#991B1B')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Approval migration feed', 'encrypted', :digest, :label)"
                    ),
                    {
                        "id": feed_id,
                        "digest": "8" * 64,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash) "
                        "VALUES (:id, :feed, 'https://example.test/item', "
                        "'Approval migration item', :dedupe, :hash)"
                    ),
                    {
                        "id": item_id,
                        "feed": feed_id,
                        "dedupe": f"approval-migration-{item_id}",
                        "hash": "9" * 64,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_task_runs "
                        "(id, task_type, trigger_source, status, item_id, "
                        "metadata_json, data_access_scope, "
                        "data_access_lineage_complete) VALUES "
                        "(:id, 'item_enrichment', 'manual', 'ready', :item, "
                        "'{}'::json, 'governed', true)"
                    ),
                    {"id": run_id, "item": item_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_task_runs "
                        "(id, task_type, trigger_source, status, metadata_json, "
                        "data_access_scope, data_access_lineage_complete) VALUES "
                        "(:id, 'connection_test', 'manual', 'ready', '{}'::json, "
                        "'system', true)"
                    ),
                    {"id": system_run_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_task_runs "
                        "(id, task_type, trigger_source, status, item_id, "
                        "metadata_json, data_access_scope, "
                        "data_access_lineage_complete) VALUES "
                        "(:id, 'item_enrichment', 'manual', 'ready', :item, "
                        "'{}'::json, 'governed', true)"
                    ),
                    {"id": other_run_id, "item": item_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'ai_task_run', :run, 1, 1)"
                    ),
                    {"id": run_envelope_id, "run": run_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_sources "
                        "(id, envelope_id, source_type, source_id, "
                        "source_version, source_feed_id, handling_label_id, "
                        "captured_policy_revision) VALUES "
                        "(:id, :envelope, 'item', :item_text, "
                        "'migration:0080:item', :feed, :label, 1)"
                    ),
                    {
                        "id": run_source_id,
                        "envelope": run_envelope_id,
                        "item_text": str(item_id),
                        "feed": feed_id,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "VALUES (:envelope, :label, 1)"
                    ),
                    {"envelope": run_envelope_id, "label": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'ai_task_run', :run, 1, 1)"
                    ),
                    {"id": other_run_envelope_id, "run": other_run_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_sources "
                        "(id, envelope_id, source_type, source_id, "
                        "source_version, source_feed_id, handling_label_id, "
                        "captured_policy_revision) VALUES "
                        "(:id, :envelope, 'item', :item_text, "
                        "'migration:0080:other-item', :feed, :label, 1)"
                    ),
                    {
                        "id": other_run_source_id,
                        "envelope": other_run_envelope_id,
                        "item_text": str(item_id),
                        "feed": feed_id,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "VALUES (:envelope, :label, 1)"
                    ),
                    {
                        "envelope": other_run_envelope_id,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_provider_attempt_receipts "
                        "(id, operation_id, attempt_number, request_fingerprint, "
                        "task_run_id_snapshot, feature_type, resource_type, "
                        "resource_id, max_attempts, requested_max_tokens, "
                        "iam_revision, data_policy_revision, data_policy_mode, "
                        "state, io_outcome, retryable, settled_at) VALUES "
                        "(:id, :operation, 1, :fingerprint, :run, "
                        "'item_enrichment', 'item', :item, 3, 1024, 1, 1, "
                        "'disabled', 'ambiguous', 'ambiguous', false, now())"
                    ),
                    {
                        "id": receipt_id,
                        "operation": uuid.uuid4(),
                        "fingerprint": "a" * 64,
                        "run": run_id,
                        "item": item_id,
                    },
                )
                _insert_legacy_approval(
                    connection,
                    approval_id=system_approval_id,
                    action_type="service_account.disable",
                    target_type="service_account",
                    target_id=str(uuid.uuid4()),
                )
                _insert_legacy_approval(
                    connection,
                    approval_id=governed_approval_id,
                    action_type="ai.provider_attempt.acknowledge_may_have_sent",
                    target_type="ai_provider_attempt_receipt",
                    target_id=str(receipt_id),
                )
                _insert_legacy_approval(
                    connection,
                    approval_id=missing_approval_id,
                    action_type="ai.provider_attempt.confirm_not_sent",
                    target_type="ai_provider_attempt_receipt",
                    target_id=str(uuid.uuid4()),
                )
                _insert_legacy_approval(
                    connection,
                    approval_id=unknown_approval_id,
                    action_type="legacy.unknown.action",
                    target_type="legacy_target",
                    target_id=str(uuid.uuid4()),
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy"):
                command.upgrade(config, "0080_action_approval_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )

            command.upgrade(config, "0080_action_approval_policy")
            inspector = inspect(schema_engine)
            columns = {
                column["name"]
                for column in inspector.get_columns(
                    "action_approval_requests", schema=schema_name
                )
            }
            assert {
                "target_data_policy_version",
                "data_access_scope",
                "data_access_lineage_complete",
                "data_access_source_type",
                "data_access_source_id",
            } <= columns

            with schema_engine.begin() as connection:
                scopes = {
                    row.id: row
                    for row in connection.execute(
                        text(
                            "SELECT id, data_access_scope, "
                            "data_access_lineage_complete, "
                            "data_access_source_type, data_access_source_id "
                            "FROM action_approval_requests"
                        )
                    ).mappings()
                }
                assert scopes[system_approval_id].data_access_scope == "system"
                assert scopes[system_approval_id].data_access_lineage_complete
                assert (
                    scopes[system_approval_id].data_access_source_type
                    == "system_control_plane"
                )
                assert scopes[governed_approval_id].data_access_scope == "governed"
                assert scopes[governed_approval_id].data_access_source_id == run_id
                assert _resource_labels(
                    connection, governed_approval_id
                ) == {restricted_label_id}
                assert _source_parent_ids(connection, governed_approval_id) == {
                    run_source_id
                }
                assert scopes[missing_approval_id].data_access_source_type == "unresolved"
                assert _resource_labels(connection, missing_approval_id) == {
                    QUARANTINE_HANDLING_LABEL_ID
                }
                assert scopes[unknown_approval_id].data_access_source_type == "unresolved"
                assert _resource_labels(connection, unknown_approval_id) == {
                    QUARANTINE_HANDLING_LABEL_ID
                }

                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 1 WHERE id = 1")
                )
                with pytest.raises(DBAPIError, match="provenance is incomplete"):
                    with connection.begin_nested():
                        _insert_legacy_approval(
                            connection,
                            approval_id=uuid.uuid4(),
                            action_type="service_account.disable",
                            target_type="service_account",
                            target_id=str(uuid.uuid4()),
                        )

                no_envelope_id = uuid.uuid4()
                with pytest.raises(DBAPIError, match="provenance is incomplete"):
                    with connection.begin_nested():
                        _insert_policy_approval(
                            connection,
                            approval_id=no_envelope_id,
                            scope="governed",
                            source_type="ai_task_run",
                            source_id=run_id,
                        )

                borrowed_system_id = uuid.uuid4()
                with pytest.raises(DBAPIError, match="provenance is incomplete"):
                    with connection.begin_nested():
                        _insert_policy_approval(
                            connection,
                            approval_id=borrowed_system_id,
                            scope="system",
                            source_type="ai_task_run",
                            source_id=system_run_id,
                            target_id=str(receipt_id),
                        )

                borrowed_governed_id = uuid.uuid4()
                borrowed_envelope_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'action_approval', :resource, 1, 1)"
                    ),
                    {
                        "id": borrowed_envelope_id,
                        "resource": borrowed_governed_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_sources "
                        "(id, envelope_id, source_type, source_id, "
                        "source_version, source_feed_id, source_parent_id, "
                        "handling_label_id, captured_policy_revision) VALUES "
                        "(:id, :envelope, 'item', :item_text, "
                        "'migration:0080:other-item', :feed, :parent, :label, 1)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "envelope": borrowed_envelope_id,
                        "item_text": str(item_id),
                        "feed": feed_id,
                        "parent": other_run_source_id,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "VALUES (:envelope, :label, 1)"
                    ),
                    {
                        "envelope": borrowed_envelope_id,
                        "label": restricted_label_id,
                    },
                )
                with pytest.raises(DBAPIError, match="provenance is incomplete"):
                    with connection.begin_nested():
                        _insert_policy_approval(
                            connection,
                            approval_id=borrowed_governed_id,
                            scope="governed",
                            source_type="ai_task_run",
                            source_id=run_id,
                            target_id=str(receipt_id),
                        )

                corrupt_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, "
                        "policy_revision) VALUES "
                        "(:id, 'action_approval', :resource, 1, 1)"
                    ),
                    {"id": uuid.uuid4(), "resource": corrupt_id},
                )
                with pytest.raises(DBAPIError, match="provenance is incomplete"):
                    with connection.begin_nested():
                        _insert_policy_approval(
                            connection,
                            approval_id=corrupt_id,
                            scope="governed",
                            source_type="ai_task_run",
                            source_id=run_id,
                        )

            _assert_trigger_serializes_lineage_deletes(
                schema_engine,
                approval_id=governed_approval_id,
                receipt_id=receipt_id,
                run_id=run_id,
            )
            _assert_trigger_matches_provider_lock_order(
                schema_engine,
                approval_id=governed_approval_id,
                receipt_id=receipt_id,
                run_id=run_id,
            )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 0 WHERE id = 1"
                    )
                )
            command.downgrade(config, "0079_metric_captured_taint")
            downgraded_columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "action_approval_requests", schema=schema_name
                )
            }
            assert "data_access_scope" not in downgraded_columns
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM data_access_envelopes "
                        "WHERE resource_type = 'action_approval'"
                    )
                ) == 0
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM data_access_envelope_sources "
                        "WHERE id = :source"
                    ),
                    {"source": run_source_id},
                ) == 1
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _insert_legacy_approval(
    connection,
    *,
    approval_id: uuid.UUID,
    action_type: str,
    target_type: str,
    target_id: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO action_approval_requests "
            "(id, action_type, action_label_snapshot, audit_action_snapshot, "
            "requester_permission_snapshot, approver_permission_snapshot, "
            "action_definition_version, target_type, target_id, "
            "target_revision, target_snapshot, payload_json, payload_digest, "
            "requested_by_email_snapshot, request_reason, expires_at, "
            "status, revision, created_at, updated_at) VALUES "
            "(:id, :action_type, 'Legacy action', 'legacy.audit', "
            "'read:approvals', 'approve:approvals', 1, :target_type, "
            ":target_id, 1, :snapshot, '{}'::jsonb, :digest, "
            "'legacy@example.test', 'Review this retained legacy action.', "
            "now() + interval '1 hour', 'pending', 1, now(), now())"
        ),
        {
            "id": approval_id,
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "snapshot": '{"precondition_digest": "' + "a" * 64 + '"}',
            "digest": "b" * 64,
        },
    )


def _assert_trigger_serializes_lineage_deletes(
    schema_engine,
    *,
    approval_id: uuid.UUID,
    receipt_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    writer = schema_engine.connect()
    writer_transaction = writer.begin()
    try:
        writer.execute(
            text(
                "UPDATE action_approval_requests "
                "SET updated_at = updated_at WHERE id = :id"
            ),
            {"id": approval_id},
        )
        for table_name, resource_id in (
            ("ai_provider_attempt_receipts", receipt_id),
            ("ai_task_runs", run_id),
        ):
            contender = schema_engine.connect()
            contender_transaction = contender.begin()
            try:
                contender.execute(text("SET LOCAL lock_timeout = '250ms'"))
                with pytest.raises(DBAPIError, match="lock timeout") as error:
                    contender.execute(
                        text(f"DELETE FROM {table_name} WHERE id = :id"),
                        {"id": resource_id},
                    )
                assert error.value.orig.sqlstate == "55P03"
            finally:
                contender_transaction.rollback()
                contender.close()
    finally:
        writer_transaction.rollback()
        writer.close()


def _assert_trigger_matches_provider_lock_order(
    schema_engine,
    *,
    approval_id: uuid.UUID,
    receipt_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    worker_pids: Queue[int] = Queue()
    worker_errors: Queue[BaseException] = Queue()

    def update_approval() -> None:
        with schema_engine.connect() as updater:
            transaction = updater.begin()
            worker_pids.put(int(updater.scalar(text("SELECT pg_backend_pid()"))))
            try:
                updater.execute(
                    text(
                        "UPDATE action_approval_requests "
                        "SET updated_at = updated_at WHERE id = :id"
                    ),
                    {"id": approval_id},
                )
                transaction.commit()
            except BaseException as exc:  # pragma: no cover - surfaced below
                worker_errors.put(exc)
                transaction.rollback()

    provider = schema_engine.connect()
    provider_transaction = provider.begin()
    worker: Thread | None = None
    try:
        provider.execute(
            text("SELECT id FROM ai_task_runs WHERE id = :id FOR UPDATE"),
            {"id": run_id},
        )
        worker = Thread(target=update_approval, daemon=True)
        worker.start()
        worker_pid = worker_pids.get(timeout=5)

        deadline = monotonic() + 5
        while monotonic() < deadline:
            with schema_engine.connect() as observer:
                wait_event_type = observer.scalar(
                    text(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": worker_pid},
                )
            if wait_event_type == "Lock":
                break
            sleep(0.01)
        else:
            raise AssertionError("Approval writer did not wait for the run lock.")

        # If the trigger took the receipt before waiting on the run, this
        # provider-order lock would close a cycle and fail with 40P01/55P03.
        provider.execute(text("SET LOCAL lock_timeout = '750ms'"))
        provider.execute(
            text(
                "SELECT id FROM ai_provider_attempt_receipts "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": receipt_id},
        )
        provider_transaction.commit()
    finally:
        if provider_transaction.is_active:
            provider_transaction.rollback()
        provider.close()
        if worker is not None:
            worker.join(timeout=5)
            assert not worker.is_alive()
    if not worker_errors.empty():
        raise worker_errors.get()


def _insert_policy_approval(
    connection,
    *,
    approval_id: uuid.UUID,
    scope: str,
    source_type: str,
    source_id: uuid.UUID | None,
    target_id: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO action_approval_requests "
            "(id, action_type, action_label_snapshot, audit_action_snapshot, "
            "requester_permission_snapshot, approver_permission_snapshot, "
            "action_definition_version, target_data_policy_version, "
            "data_access_scope, data_access_lineage_complete, "
            "data_access_source_type, data_access_source_id, target_type, "
            "target_id, target_revision, target_snapshot, payload_json, "
            "payload_digest, requested_by_email_snapshot, request_reason, "
            "expires_at, status, revision, created_at, updated_at) VALUES "
            "(:id, 'ai.provider_attempt.confirm_not_sent', 'Legacy action', "
            "'legacy.audit', 'read:ai', 'write:ai', 1, 1, :scope, true, "
            ":source_type, :source_id, 'ai_provider_attempt_receipt', "
            ":target_id, 1, :snapshot, '{}'::jsonb, :digest, "
            "'legacy@example.test', 'Review this retained legacy action.', "
            "now() + interval '1 hour', 'pending', 1, now(), now())"
        ),
        {
            "id": approval_id,
            "scope": scope,
            "source_type": source_type,
            "source_id": source_id,
            "target_id": target_id or str(uuid.uuid4()),
            "snapshot": '{"precondition_digest": "' + "c" * 64 + '"}',
            "digest": "d" * 64,
        },
    )


def _resource_labels(connection, approval_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        connection.scalars(
            text(
                "SELECT label.label_id "
                "FROM data_access_envelope_labels AS label "
                "JOIN data_access_envelopes AS envelope "
                "ON envelope.id = label.envelope_id "
                "WHERE envelope.resource_type = 'action_approval' "
                "AND envelope.resource_id = :id"
            ),
            {"id": approval_id},
        )
    )


def _source_parent_ids(connection, approval_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        connection.scalars(
            text(
                "SELECT source.source_parent_id "
                "FROM data_access_envelope_sources AS source "
                "JOIN data_access_envelopes AS envelope "
                "ON envelope.id = source.envelope_id "
                "WHERE envelope.resource_type = 'action_approval' "
                "AND envelope.resource_id = :id "
                "AND source.source_parent_id IS NOT NULL"
            ),
            {"id": approval_id},
        )
    )
