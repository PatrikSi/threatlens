from __future__ import annotations

import json
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


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


def test_report_ready_owner_migration_upgrades_valid_events_and_quarantines_unsafe_ones(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0072_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    owner_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    report_id = uuid.uuid4()
    other_report_id = uuid.uuid4()
    valid_event_id = uuid.uuid4()
    mismatched_event_id = uuid.uuid4()
    actorless_event_id = uuid.uuid4()
    wrong_owner_event_id = uuid.uuid4()
    malformed_event_id = uuid.uuid4()
    wrong_type_event_id = uuid.uuid4()
    post_migration_event_id = uuid.uuid4()
    post_migration_malformed_event_id = uuid.uuid4()
    global_smtp_id = uuid.uuid4()
    global_subscription_id = uuid.uuid4()
    global_delivery_id = uuid.uuid4()
    orphan_delivery_id = uuid.uuid4()
    wrong_event_delivery_id = uuid.uuid4()
    other_webhook_integration_id = uuid.uuid4()
    other_webhook_subscription_id = uuid.uuid4()
    other_webhook_id = uuid.uuid4()
    unauthorized_delivery_id = uuid.uuid4()
    unauthorized_attempt_id = uuid.uuid4()
    unauthorized_projection_id = uuid.uuid4()

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
            command.upgrade(config, "0071_data_access_lineage")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_active, is_approved) VALUES "
                        "(:id, 'migration-0072@example.com', 'hash', true, true), "
                        "(:other_id, 'migration-0072-other@example.com', 'hash', "
                        "true, true)"
                    ),
                    {"id": owner_id, "other_id": other_user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO reports "
                        "(id, owner_user_id, title, period_start, period_end, "
                        "filters_json, prompt_config_json, sections_config_json, "
                        "metrics_json, coverage_json) VALUES "
                        "(:report_id, :owner_id, 'Report', now(), now(), '{}'::json, "
                        "'{}'::json, '[]'::json, '{}'::json, '{}'::json), "
                        "(:other_id, :owner_id, 'Other', now(), now(), '{}'::json, "
                        "'{}'::json, '[]'::json, '{}'::json, '{}'::json)"
                    ),
                    {
                        "report_id": report_id,
                        "other_id": other_report_id,
                        "owner_id": owner_id,
                    },
                )
                valid_payload = {
                    "schema_version": 1,
                    "report_id": str(report_id),
                    "daily_brief": {"id": str(report_id)},
                }
                mismatched_payload = {
                    "schema_version": 1,
                    "report_id": str(report_id),
                    "daily_brief": {"id": str(report_id)},
                }
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "actor_user_id, idempotency_key, payload_json, routing_state) "
                        "VALUES "
                        "(:valid_id, 'report_ready', 1, 'report', :report_id, "
                        ":owner_id, 'migration-0072-valid', CAST(:valid_payload AS json), "
                        "'pending'), "
                        "(:mismatch_id, 'report_ready', 1, 'report', :other_id, "
                        ":owner_id, 'migration-0072-mismatch', "
                        "CAST(:mismatch_payload AS json), 'pending'), "
                        "(:actorless_id, 'report_ready', 1, 'report', :report_id, "
                        "NULL, 'migration-0072-actorless', "
                        "CAST(:valid_payload AS json), 'failed'), "
                        "(:wrong_owner_id, 'report_ready', 1, 'report', :report_id, "
                        ":other_user_id, 'migration-0072-wrong-owner', "
                        "CAST(:valid_payload AS json), 'pending'), "
                        "(:malformed_id, 'report_ready', 1, 'report', :report_id, "
                        ":owner_id, 'migration-0072-malformed', '{}'::json, 'routed')"
                    ),
                    {
                        "valid_id": valid_event_id,
                        "mismatch_id": mismatched_event_id,
                        "actorless_id": actorless_event_id,
                        "wrong_owner_id": wrong_owner_event_id,
                        "other_user_id": other_user_id,
                        "malformed_id": malformed_event_id,
                        "report_id": str(report_id),
                        "other_id": str(other_report_id),
                        "owner_id": owner_id,
                        "valid_payload": json.dumps(valid_payload),
                        "mismatch_payload": json.dumps(mismatched_payload),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "actor_user_id, idempotency_key, payload_json, routing_state) "
                        "VALUES (:id, 'rss_item_new', 1, 'item', :source_id, "
                        ":owner_id, 'migration-0072-wrong-type', '{}'::json, 'routed')"
                    ),
                    {
                        "id": wrong_type_event_id,
                        "source_id": str(uuid.uuid4()),
                        "owner_id": owner_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_instances "
                        "(id, owner_user_id, system_key, name, integration_type, "
                        "direction, enabled, config_json) VALUES "
                        "(:smtp_id, NULL, 'smtp.migration-0072', 'Global SMTP', "
                        "'smtp', 'destination', true, '{}'::json), "
                        "(:webhook_integration_id, :other_id, NULL, "
                        "'Other webhook', 'webhook', 'destination', true, '{}'::json)"
                    ),
                    {
                        "smtp_id": global_smtp_id,
                        "webhook_integration_id": other_webhook_integration_id,
                        "other_id": other_user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_subscriptions "
                        "(id, integration_id, subscription_key, event_type, enabled) "
                        "VALUES "
                        "(:smtp_subscription_id, :smtp_id, 'event:report_ready', "
                        "'report_ready', true), "
                        "(:webhook_subscription_id, :webhook_integration_id, "
                        "'event:report_ready', 'report_ready', true)"
                    ),
                    {
                        "smtp_subscription_id": global_subscription_id,
                        "smtp_id": global_smtp_id,
                        "webhook_subscription_id": other_webhook_subscription_id,
                        "webhook_integration_id": other_webhook_integration_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhooks "
                        "(id, user_id, integration_id, subscription_id, name, enabled, "
                        "event_type, url_template, method, feed_scope, feed_ids_json, "
                        "query_params_json, headers_json, body_mode, body_fields_json) "
                        "VALUES (:id, :other_id, :integration_id, :subscription_id, "
                        "'Other report webhook', true, 'report_ready', "
                        "'https://example.com/report', 'POST', 'all', '[]'::json, "
                        "'[]'::json, '[]'::json, 'none', '[]'::json)"
                    ),
                    {
                        "id": other_webhook_id,
                        "other_id": other_user_id,
                        "integration_id": other_webhook_integration_id,
                        "subscription_id": other_webhook_subscription_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_deliveries "
                        "(id, integration_id, subscription_id, event_id, owner_user_id, "
                        "connector_type, event_type, delivery_kind, state, "
                        "idempotency_key, payload_json) VALUES "
                        "(:global_id, :smtp_id, :smtp_subscription_id, :event_id, NULL, "
                        "'smtp', 'report_ready', 'live', 'pending', "
                        "'migration-0072-global', CAST(:payload AS json)), "
                        "(:unauthorized_id, :webhook_integration_id, "
                        ":webhook_subscription_id, :event_id, :other_id, 'webhook', "
                        "'report_ready', 'live', 'sending', "
                        "'migration-0072-unauthorized', CAST(:payload AS json)), "
                        "(:orphan_id, :smtp_id, :smtp_subscription_id, NULL, NULL, "
                        "'smtp', 'report_ready', 'live', 'pending', "
                        "'migration-0072-orphan', CAST(:payload AS json)), "
                        "(:wrong_event_delivery_id, :smtp_id, :smtp_subscription_id, "
                        ":wrong_event_id, NULL, 'smtp', 'report_ready', 'live', "
                        "'sending', 'migration-0072-wrong-event', "
                        "CAST(:payload AS json))"
                    ),
                    {
                        "global_id": global_delivery_id,
                        "smtp_id": global_smtp_id,
                        "smtp_subscription_id": global_subscription_id,
                        "event_id": valid_event_id,
                        "unauthorized_id": unauthorized_delivery_id,
                        "webhook_integration_id": other_webhook_integration_id,
                        "webhook_subscription_id": other_webhook_subscription_id,
                        "other_id": other_user_id,
                        "orphan_id": orphan_delivery_id,
                        "wrong_event_delivery_id": wrong_event_delivery_id,
                        "wrong_event_id": wrong_type_event_id,
                        "payload": json.dumps(valid_payload),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_attempts "
                        "(id, delivery_id, integration_id, attempt_number, status, "
                        "response_json) VALUES (:id, :delivery_id, :integration_id, "
                        "1, 'running', '{}'::json)"
                    ),
                    {
                        "id": unauthorized_attempt_id,
                        "delivery_id": unauthorized_delivery_id,
                        "integration_id": other_webhook_integration_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhook_deliveries "
                        "(id, integration_delivery_id, webhook_id, user_id, "
                        "event_type_snapshot, delivery_state, success, rendered_url, "
                        "rendered_method, rendered_headers_json, "
                        "rendered_query_params_json) VALUES "
                        "(:id, :delivery_id, :webhook_id, :other_id, 'report_ready', "
                        "'sending', false, 'https://example.com/report', 'POST', "
                        "'[]'::json, '[]'::json)"
                    ),
                    {
                        "id": unauthorized_projection_id,
                        "delivery_id": unauthorized_delivery_id,
                        "webhook_id": other_webhook_id,
                        "other_id": other_user_id,
                    },
                )

            command.upgrade(config, "0072_report_ready_owner_envelope")
            with schema_engine.connect() as connection:
                valid = connection.execute(
                    text(
                        "SELECT schema_version, payload_json, routing_state "
                        "FROM integration_events WHERE id = :id"
                    ),
                    {"id": valid_event_id},
                ).one()
                assert valid.schema_version == 2
                assert valid.payload_json["schema_version"] == 2
                assert valid.payload_json["owner_user_id"] == str(owner_id)
                assert valid.routing_state == "pending"

                unsafe = dict(
                    connection.execute(
                        text(
                            "SELECT id, routing_state FROM integration_events "
                            "WHERE id IN (:mismatch_id, :actorless_id, "
                            ":wrong_owner_id, :malformed_id)"
                        ),
                        {
                            "mismatch_id": mismatched_event_id,
                            "actorless_id": actorless_event_id,
                            "wrong_owner_id": wrong_owner_event_id,
                            "malformed_id": malformed_event_id,
                        },
                    ).all()
                )
                assert unsafe == {
                    mismatched_event_id: "dead_letter",
                    actorless_event_id: "dead_letter",
                    wrong_owner_event_id: "dead_letter",
                    malformed_event_id: "dead_letter",
                }

                global_delivery = connection.execute(
                    text(
                        "SELECT state, owner_user_id, payload_json "
                        "FROM integration_deliveries WHERE id = :id"
                    ),
                    {"id": global_delivery_id},
                ).one()
                assert global_delivery.state == "pending"
                assert global_delivery.owner_user_id == owner_id
                assert global_delivery.payload_json["owner_user_id"] == str(owner_id)
                assert global_delivery.payload_json["schema_version"] == 2

                unauthorized_delivery = connection.execute(
                    text(
                        "SELECT state, last_error_code FROM integration_deliveries "
                        "WHERE id = :id"
                    ),
                    {"id": unauthorized_delivery_id},
                ).one()
                assert unauthorized_delivery == (
                    "dead_letter",
                    "report_owner_envelope_invalid",
                )
                quarantined_delivery_ids = set(
                    connection.scalars(
                        text(
                            "SELECT id FROM integration_deliveries "
                            "WHERE id IN (:orphan_id, :wrong_event_id) "
                            "AND state = 'dead_letter' "
                            "AND last_error_code = 'report_owner_envelope_invalid'"
                        ),
                        {
                            "orphan_id": orphan_delivery_id,
                            "wrong_event_id": wrong_event_delivery_id,
                        },
                    ).all()
                )
                assert quarantined_delivery_ids == {
                    orphan_delivery_id,
                    wrong_event_delivery_id,
                }
                assert (
                    connection.scalar(
                        text("SELECT status FROM integration_attempts WHERE id = :id"),
                        {"id": unauthorized_attempt_id},
                    )
                    == "failed"
                )
                projection = connection.execute(
                    text(
                        "SELECT delivery_state, success, error "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": unauthorized_projection_id},
                ).one()
                assert projection.delivery_state == "failed"
                assert projection.success is False
                assert projection.error.startswith("policy_error:")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, schema_version, source_type, source_id, "
                        "actor_user_id, idempotency_key, payload_json, routing_state) "
                        "VALUES (:valid_id, 'report_ready', 1, 'report', :report_id, "
                        ":owner_id, 'migration-0072-post-valid', "
                        "CAST(:payload AS json), 'pending'), "
                        "(:malformed_id, 'report_ready', 1, 'report', :report_id, "
                        ":owner_id, 'migration-0072-post-malformed', '{}'::json, "
                        "'pending')"
                    ),
                    {
                        "valid_id": post_migration_event_id,
                        "malformed_id": post_migration_malformed_event_id,
                        "report_id": str(report_id),
                        "owner_id": owner_id,
                        "payload": json.dumps(valid_payload),
                    },
                )
                post_migration = connection.execute(
                    text(
                        "SELECT id, schema_version, routing_state, payload_json "
                        "FROM integration_events "
                        "WHERE id IN (:valid_id, :malformed_id)"
                    ),
                    {
                        "valid_id": post_migration_event_id,
                        "malformed_id": post_migration_malformed_event_id,
                    },
                ).all()
                post_by_id = {row.id: row for row in post_migration}
                assert post_by_id[post_migration_event_id].schema_version == 2
                assert post_by_id[post_migration_event_id].payload_json[
                    "owner_user_id"
                ] == str(owner_id)
                assert (
                    post_by_id[post_migration_malformed_event_id].routing_state
                    == "dead_letter"
                )

            command.downgrade(config, "0071_data_access_lineage")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT schema_version FROM integration_events WHERE id = :id"
                        ),
                        {"id": valid_event_id},
                    )
                    == 2
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
