from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_V2_TABLES = {
    "alert_backfill_previews",
    "alert_evaluation_matches",
    "alert_evaluation_requests",
    "alert_evaluation_request_activities",
    "alert_occurrences",
    "alert_occurrence_activities",
    "alert_occurrence_metrics",
}


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    return url.render_as_string(hide_password=False)


def test_alerting_v2_populated_upgrade_downgrade_has_explicit_future_cutover(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0059_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    user_id = uuid.uuid4()
    enabled_alert_id = uuid.uuid4()
    disabled_alert_id = uuid.uuid4()
    legacy_alert_id = uuid.uuid4()
    legacy_enabled_alert_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    new_item_id = uuid.uuid4()
    legacy_event_id = uuid.uuid4()
    durable_event_id = uuid.uuid4()
    downgrade_legacy_event_id = uuid.uuid4()

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
            command.upgrade(config, "0058_investigations")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, role, is_active, is_approved) "
                        "VALUES (:id, 'alerts@example.com', 'hash', 'viewer', TRUE, TRUE)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_interests "
                        "(id, user_id, name, category, keywords, enabled) VALUES "
                        "(:enabled_id, :user_id, 'Enabled', 'threat', '[\"fortinet\"]'::json, TRUE), "
                        "(:disabled_id, :user_id, 'Disabled', 'threat', '[\"malware\"]'::json, FALSE)"
                    ),
                    {
                        "enabled_id": enabled_alert_id,
                        "disabled_id": disabled_alert_id,
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds (id, name, url, url_digest, enabled) "
                        "VALUES (:id, 'Historical feed', 'encrypted', :digest, TRUE)"
                    ),
                    {"id": feed_id, "digest": "f" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash, status, first_seen_at) "
                        "VALUES (:id, :feed_id, 'https://example.com/old', 'Fortinet old item', "
                        "'old-item', :content_hash, 'content_fetched', :first_seen_at)"
                    ),
                    {
                        "id": item_id,
                        "feed_id": feed_id,
                        "content_hash": "a" * 64,
                        "first_seen_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "idempotency_key, payload_json, routing_state) VALUES "
                        "(:id, 'alert_match', 2, 'item', :source_id, :key, "
                        "CAST(:payload AS json), 'pending')"
                    ),
                    {
                        "id": legacy_event_id,
                        "source_id": str(item_id),
                        "key": f"legacy-alert-event:{legacy_event_id}",
                        "payload": json.dumps(
                            {"schema_version": 2, "item_id": str(item_id)}
                        ),
                    },
                )

            before_upgrade = datetime.now(timezone.utc)
            command.upgrade(config, "0059_alerting_v2")
            inspector = inspect(schema_engine)
            assert _V2_TABLES <= set(inspector.get_table_names(schema=schema_name))
            alert_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "alert_interests", schema=schema_name
                )
            }
            assert alert_columns >= {
                "severity",
                "revision",
                "row_version",
                "durable_since",
                "suppression_until",
                "suppression_reason",
            }
            alert_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "alert_interests", schema=schema_name
                )
            }
            occurrence_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "alert_occurrences", schema=schema_name
                )
            }
            assert alert_checks >= {
                "ck_alert_interests_revision",
                "ck_alert_interests_row_version",
                "ck_alert_interests_suppression_pair",
            }
            assert occurrence_checks >= {
                "ck_alert_occurrences_closed_disposition",
                "ck_alert_occurrences_suppression_pair",
                "ck_alert_occurrences_snooze_pair",
            }
            assert {
                index["name"]
                for index in inspector.get_indexes(
                    "alert_evaluation_requests", schema=schema_name
                )
            } >= {
                "ix_alert_evaluation_requests_recovery",
                "ix_alert_evaluation_requests_retention",
                "ix_alert_evaluation_requests_dispatch_failure",
            }
            request_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "alert_evaluation_requests", schema=schema_name
                )
            }
            assert request_columns >= {
                "notify_existing_occurrences",
                "dispatch_failure_count",
                "last_dispatch_failed_at",
            }
            assert {
                index["name"]
                for index in inspector.get_indexes(
                    "alert_backfill_previews", schema=schema_name
                )
            } >= {
                "ix_alert_backfill_previews_actor_expiry",
                "ix_alert_backfill_previews_expiry",
            }
            assert {
                index["name"]
                for index in inspector.get_indexes(
                    "alert_occurrences", schema=schema_name
                )
            } >= {
                "ix_alert_occurrences_owner_state_created",
                "ix_alert_occurrences_retention",
            }
            with schema_engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alert_interests_v2_compat' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alerting_v2_event_fence' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 1
                )
                queued_legacy_event = (
                    connection.execute(
                        text(
                            "SELECT routing_state, last_error FROM integration_events "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_event_id},
                    )
                    .mappings()
                    .one()
                )
                assert queued_legacy_event["routing_state"] == "pending"
                assert queued_legacy_event["last_error"] is None

            with schema_engine.begin() as connection:
                with pytest.raises(DBAPIError, match="legacy alert_match event"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO integration_events "
                                "(id, event_type, schema_version, source_type, source_id, "
                                "idempotency_key, payload_json) VALUES "
                                "(:id, 'alert_match', 2, 'item', :source_id, :key, "
                                "CAST(:payload AS json))"
                            ),
                            {
                                "id": uuid.uuid4(),
                                "source_id": str(item_id),
                                "key": f"rejected-legacy-alert-event:{uuid.uuid4()}",
                                "payload": json.dumps(
                                    {"schema_version": 2, "item_id": str(item_id)}
                                ),
                            },
                        )
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "idempotency_key, payload_json) VALUES "
                        "(:id, 'alert_match', 3, 'item', :source_id, :key, "
                        "CAST(:payload AS json))"
                    ),
                    {
                        "id": durable_event_id,
                        "source_id": str(item_id),
                        "key": f"durable-alert-event:{durable_event_id}",
                        "payload": json.dumps(
                            {
                                "schema_version": 3,
                                "item_id": str(item_id),
                                "evaluation_request_id": str(uuid.uuid4()),
                            }
                        ),
                    },
                )
                connection.execute(
                    text("DELETE FROM integration_events WHERE id = :id"),
                    {"id": durable_event_id},
                )

            explicit_cutover = datetime(2030, 1, 1, tzinfo=timezone.utc)
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO alert_interests "
                        "(id, user_id, name, category, keywords, enabled) "
                        "VALUES (:id, :user_id, 'Legacy enabled writer', 'threat', "
                        "'[\"enabled\"]'::json, TRUE)"
                    ),
                    {"id": legacy_enabled_alert_id, "user_id": user_id},
                )
                legacy_enabled = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_enabled_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert legacy_enabled["revision"] == 1
                assert legacy_enabled["row_version"] == 1
                assert legacy_enabled["durable_since"] is not None

                connection.execute(
                    text(
                        "INSERT INTO alert_interests "
                        "(id, user_id, name, category, keywords, enabled) "
                        "VALUES (:id, :user_id, 'Legacy writer', 'threat', "
                        "'[\"legacy\"]'::json, FALSE)"
                    ),
                    {"id": legacy_alert_id, "user_id": user_id},
                )
                inserted = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert inserted["revision"] == 1
                assert inserted["row_version"] == 1
                assert inserted["durable_since"] is None

                connection.execute(
                    text("UPDATE alert_interests SET enabled = TRUE WHERE id = :id"),
                    {"id": legacy_alert_id},
                )
                enabled = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert enabled["revision"] == 2
                assert enabled["row_version"] == 2
                assert enabled["durable_since"] is not None

                connection.execute(
                    text(
                        "UPDATE alert_interests SET name = 'Legacy semantic edit' "
                        "WHERE id = :id"
                    ),
                    {"id": legacy_alert_id},
                )
                semantic = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert semantic["revision"] == 3
                assert semantic["row_version"] == 3
                assert semantic["durable_since"] >= enabled["durable_since"]

                connection.execute(
                    text(
                        "UPDATE alert_interests SET updated_at = updated_at + INTERVAL '1 second' "
                        "WHERE id = :id"
                    ),
                    {"id": legacy_alert_id},
                )
                nonsemantic = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert nonsemantic == semantic

                connection.execute(
                    text(
                        "UPDATE alert_interests SET category = 'v2_edit', "
                        "revision = revision + 1, row_version = row_version + 1, "
                        "durable_since = :cutover "
                        "WHERE id = :id"
                    ),
                    {"id": legacy_alert_id, "cutover": explicit_cutover},
                )
                explicit = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert explicit["revision"] == 4
                assert explicit["row_version"] == 4
                assert explicit["durable_since"] == explicit_cutover

                connection.execute(
                    text(
                        "UPDATE alert_interests SET suppression_until = :until, "
                        "suppression_reason = 'Legacy maintenance' WHERE id = :id"
                    ),
                    {
                        "id": legacy_alert_id,
                        "until": datetime(2031, 1, 1, tzinfo=timezone.utc),
                    },
                )
                suppressed = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert suppressed["revision"] == 4
                assert suppressed["row_version"] == 5
                assert suppressed["durable_since"] == explicit_cutover

                with pytest.raises(IntegrityError, match="revision cannot change"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE alert_interests SET "
                                "suppression_reason = 'Invalid revision jump', "
                                "revision = revision + 7, row_version = row_version + 1 "
                                "WHERE id = :id"
                            ),
                            {"id": legacy_alert_id},
                        )

                with pytest.raises(IntegrityError, match="row version cannot change"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE alert_interests SET row_version = row_version + 7 "
                                "WHERE id = :id"
                            ),
                            {"id": legacy_alert_id},
                        )

                connection.execute(
                    text("UPDATE alert_interests SET enabled = FALSE WHERE id = :id"),
                    {"id": legacy_alert_id},
                )
                disabled = (
                    connection.execute(
                        text(
                            "SELECT revision, row_version, durable_since FROM alert_interests "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_alert_id},
                    )
                    .mappings()
                    .one()
                )
                assert disabled["revision"] == 4
                assert disabled["row_version"] == 6
                assert disabled["durable_since"] is None

            with schema_engine.connect() as connection:
                rows = (
                    connection.execute(
                        text(
                            "SELECT id, severity, revision, row_version, durable_since "
                            "FROM alert_interests "
                            "ORDER BY name"
                        )
                    )
                    .mappings()
                    .all()
                )
                by_id = {row["id"]: row for row in rows}
                assert by_id[enabled_alert_id]["severity"] == "medium"
                assert by_id[enabled_alert_id]["revision"] == 1
                assert by_id[enabled_alert_id]["row_version"] == 1
                assert by_id[enabled_alert_id]["durable_since"] >= before_upgrade
                assert by_id[disabled_alert_id]["durable_since"] is None
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM alert_interests a JOIN items i ON i.id = :item_id "
                            "WHERE a.id = :alert_id AND a.enabled IS TRUE "
                            "AND a.durable_since <= i.first_seen_at"
                        ),
                        {"item_id": item_id, "alert_id": enabled_alert_id},
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM alert_interests "
                            "WHERE id = :alert_id AND enabled IS TRUE"
                        ),
                        {"alert_id": enabled_alert_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM alert_evaluation_requests")
                    ).scalar_one()
                    == 0
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE alert_interests SET category = 'threat_v2', revision = 2, "
                        "durable_since = CURRENT_TIMESTAMP WHERE id = :alert_id"
                    ),
                    {"alert_id": enabled_alert_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash, status, first_seen_at) "
                        "SELECT :id, :feed_id, 'https://example.com/new', 'Fortinet new item', "
                        "'new-item', :content_hash, 'content_fetched', durable_since + INTERVAL '1 second' "
                        "FROM alert_interests WHERE id = :alert_id"
                    ),
                    {
                        "id": new_item_id,
                        "feed_id": feed_id,
                        "content_hash": "b" * 64,
                        "alert_id": enabled_alert_id,
                    },
                )
                eligibility = connection.execute(
                    text(
                        "SELECT i.id, count(a.id) AS eligible FROM items i "
                        "LEFT JOIN alert_interests a ON a.id = :alert_id "
                        "AND a.enabled IS TRUE AND a.durable_since <= i.first_seen_at "
                        "WHERE i.id IN (:old_item_id, :new_item_id) GROUP BY i.id"
                    ),
                    {
                        "alert_id": enabled_alert_id,
                        "old_item_id": item_id,
                        "new_item_id": new_item_id,
                    },
                ).mappings()
                assert {row["id"]: row["eligible"] for row in eligibility} == {
                    item_id: 0,
                    new_item_id: 1,
                }
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM alert_occurrences")
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM integration_events "
                            "WHERE event_type = 'alert_match' "
                            "AND routing_state <> 'dead_letter'"
                        )
                    ).scalar_one()
                    == 1
                )

            with schema_engine.begin() as connection:
                request_id = uuid.uuid4()
                pending_request_id = uuid.uuid4()
                occurrence_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO alert_evaluation_requests "
                        "(id, item_id, item_content_hash, state, completed_at) "
                        "VALUES (:id, :item_id, :content_hash, 'succeeded', CURRENT_TIMESTAMP)"
                    ),
                    {"id": request_id, "item_id": item_id, "content_hash": "a" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_evaluation_requests "
                        "(id, item_id, item_content_hash, state) "
                        "VALUES (:id, :item_id, :content_hash, 'pending')"
                    ),
                    {
                        "id": pending_request_id,
                        "item_id": new_item_id,
                        "content_hash": "b" * 64,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrences "
                        "(id, alert_interest_id, rule_id_snapshot, owner_user_id, item_id, "
                        "item_id_snapshot, rule_revision, item_content_hash, "
                        "alert_name_snapshot, alert_category_snapshot, severity_snapshot) VALUES "
                        "(:id, :alert_id, :alert_id, :user_id, :item_id, :item_id, 1, "
                        ":content_hash, 'Enabled', 'threat', 'medium')"
                    ),
                    {
                        "id": occurrence_id,
                        "alert_id": enabled_alert_id,
                        "user_id": user_id,
                        "item_id": item_id,
                        "content_hash": "a" * 64,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_activities "
                        "(id, occurrence_id, action) VALUES (:id, :occurrence_id, 'created')"
                    ),
                    {"id": uuid.uuid4(), "occurrence_id": occurrence_id},
                )

            with pytest.raises(DBAPIError, match="alert evaluations are nonterminal"):
                command.downgrade(config, "0058_investigations")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE alert_evaluation_requests "
                        "SET state = 'dead_letter', completed_at = CURRENT_TIMESTAMP "
                        "WHERE id = :id"
                    ),
                    {"id": pending_request_id},
                )
                pending_schema_3_event_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "idempotency_key, payload_json, routing_state) VALUES "
                        "(:id, 'alert_match', 3, 'item', :source_id, :key, "
                        "CAST(:payload AS json), 'pending')"
                    ),
                    {
                        "id": pending_schema_3_event_id,
                        "source_id": str(item_id),
                        "key": f"pending-schema-3-alert:{pending_schema_3_event_id}",
                        "payload": json.dumps(
                            {
                                "schema_version": 3,
                                "item_id": str(item_id),
                                "owner_user_id": str(user_id),
                                "evaluation_request_id": str(request_id),
                            }
                        ),
                    },
                )

            with pytest.raises(DBAPIError, match="schema-3 alert events are nonterminal"):
                command.downgrade(config, "0058_investigations")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE integration_events "
                        "SET routing_state = 'routed', routed_at = CURRENT_TIMESTAMP "
                        "WHERE id = :id"
                    ),
                    {"id": pending_schema_3_event_id},
                )

            command.downgrade(config, "0058_investigations")
            downgraded_inspector = inspect(schema_engine)
            assert not (
                _V2_TABLES
                & set(downgraded_inspector.get_table_names(schema=schema_name))
            )
            downgraded_columns = {
                column["name"]
                for column in downgraded_inspector.get_columns(
                    "alert_interests", schema=schema_name
                )
            }
            assert (
                not {
                    "severity",
                    "revision",
                    "row_version",
                    "durable_since",
                    "suppression_until",
                    "suppression_reason",
                }
                & downgraded_columns
            )
            with schema_engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM alert_interests")
                    ).scalar_one()
                    == 4
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alert_interests_v2_compat' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alerting_v2_event_fence' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 0
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "idempotency_key, payload_json) VALUES "
                        "(:id, 'alert_match', 2, 'item', :source_id, :key, "
                        "CAST(:payload AS json))"
                    ),
                    {
                        "id": downgrade_legacy_event_id,
                        "source_id": str(item_id),
                        "key": f"downgrade-legacy-alert:{downgrade_legacy_event_id}",
                        "payload": json.dumps(
                            {"schema_version": 2, "item_id": str(item_id)}
                        ),
                    },
                )

            command.upgrade(config, "0059_alerting_v2")
            assert _V2_TABLES <= set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )
            with schema_engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alert_interests_v2_compat' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE t.tgname = 'trg_alerting_v2_event_fence' "
                            "AND n.nspname = current_schema() "
                            "AND NOT tgisinternal"
                        )
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT routing_state FROM integration_events WHERE id = :id"
                        ),
                        {"id": downgrade_legacy_event_id},
                    ).scalar_one()
                    == "pending"
                )
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
