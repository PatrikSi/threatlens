from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)


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


def test_data_access_envelope_migration_backfills_and_quarantines_new_feeds(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0070_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    existing_feed_id = uuid.uuid4()
    restricted_feed_id = uuid.uuid4()
    old_process_feed_id = uuid.uuid4()
    restricted_label_id = uuid.uuid4()
    user_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    failed_delivery_id = uuid.uuid4()
    feed_event_id = uuid.uuid4()
    webhook_failed_event_id = uuid.uuid4()
    report_id = uuid.uuid4()

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
            command.upgrade(config, "0069_data_policy_foundation")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO feeds (id, name, url, url_digest) "
                        "VALUES (:id, 'Existing feed', 'encrypted', :digest)"
                    ),
                    {"id": existing_feed_id, "digest": "a" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO handling_labels (id, key, name, color) "
                        "VALUES (:id, 'restricted-migration', "
                        "'Restricted migration', '#B91C1C')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) "
                        "VALUES (:id, 'Restricted feed', 'encrypted', :digest, "
                        ":label_id)"
                    ),
                    {
                        "id": restricted_feed_id,
                        "digest": "c" * 64,
                        "label_id": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_approved) "
                        "VALUES (:id, 'migration-0070@example.com', 'hash', true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhooks "
                        "(id, user_id, name, url_template, feed_ids_json, "
                        "query_params_json, headers_json, body_fields_json) "
                        "VALUES (:id, :user_id, 'Migration webhook', "
                        "'https://example.com/hook', '[]'::json, '[]'::json, "
                        "'[]'::json, '[]'::json)"
                    ),
                    {"id": webhook_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhook_deliveries "
                        "(id, webhook_id, user_id, event_type_snapshot, feed_id, "
                        "rendered_url, rendered_method, rendered_headers_json, "
                        "rendered_query_params_json) VALUES "
                        "(:id, :webhook_id, :user_id, 'rss_item_new', :feed_id, "
                        "'https://example.com/hook', 'POST', '[]'::json, '[]'::json)"
                    ),
                    {
                        "id": failed_delivery_id,
                        "webhook_id": webhook_id,
                        "user_id": user_id,
                        "feed_id": restricted_feed_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, source_type, source_id, idempotency_key, "
                        "payload_json) VALUES "
                        "(:feed_event_id, 'feed_failing', 'feed', :feed_id, "
                        ":feed_key, '{}'::json), "
                        "(:failed_event_id, 'webhook_failed', "
                        "'notification_webhook_delivery', :delivery_id, "
                        ":failed_key, '{}'::json)"
                    ),
                    {
                        "feed_event_id": feed_event_id,
                        "feed_id": str(restricted_feed_id),
                        "feed_key": f"feed-event:{feed_event_id}",
                        "failed_event_id": webhook_failed_event_id,
                        "delivery_id": str(failed_delivery_id),
                        "failed_key": f"failed-event:{webhook_failed_event_id}",
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO reports (
                            id, title, period_start, period_end, filters_json,
                            prompt_config_json, sections_config_json,
                            metrics_json, coverage_json
                        ) VALUES (
                            :id, 'Existing report', now(), now(),
                            '{}'::json, '{}'::json, '[]'::json,
                            '{}'::json, '{}'::json
                        )
                        """
                    ),
                    {"id": report_id},
                )

            command.upgrade(config, "0070_data_access_envelopes")
            inspector = inspect(schema_engine)
            assert {
                "data_access_envelopes",
                "data_access_envelope_labels",
            } <= set(inspector.get_table_names(schema=schema_name))

            with schema_engine.begin() as connection:
                quarantine = connection.execute(
                    text(
                        "SELECT key, is_unrestricted, is_system, is_active "
                        "FROM handling_labels WHERE id = :id"
                    ),
                    {"id": QUARANTINE_HANDLING_LABEL_ID},
                ).one()
                assert quarantine == ("quarantine", False, True, True)
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM data_policy_role_grants "
                        "WHERE label_id = :label_id AND role_id = :role_id"
                    ),
                    {
                        "label_id": QUARANTINE_HANDLING_LABEL_ID,
                        "role_id": SYSTEM_ROLE_IDS["admin"],
                    },
                ) == 1
                assert connection.scalar(
                    text("SELECT handling_label_id FROM feeds WHERE id = :id"),
                    {"id": existing_feed_id},
                ) == UNRESTRICTED_HANDLING_LABEL_ID

                connection.execute(
                    text(
                        "INSERT INTO feeds (id, name, url, url_digest) "
                        "VALUES (:id, 'Old process feed', 'encrypted', :digest)"
                    ),
                    {"id": old_process_feed_id, "digest": "b" * 64},
                )
                assert connection.scalar(
                    text("SELECT handling_label_id FROM feeds WHERE id = :id"),
                    {"id": old_process_feed_id},
                ) == QUARANTINE_HANDLING_LABEL_ID

                report_envelope = connection.execute(
                    text(
                        """
                        SELECT envelope.resource_type, label.label_id
                        FROM data_access_envelopes AS envelope
                        JOIN data_access_envelope_labels AS label
                          ON label.envelope_id = envelope.id
                        WHERE envelope.resource_type = 'report'
                          AND envelope.resource_id = :report_id
                        """
                    ),
                    {"report_id": report_id},
                ).one()
                assert report_envelope == ("report", UNRESTRICTED_HANDLING_LABEL_ID)
                event_labels = dict(
                    connection.execute(
                        text(
                            """
                            SELECT envelope.resource_id, label.label_id
                            FROM data_access_envelopes AS envelope
                            JOIN data_access_envelope_labels AS label
                              ON label.envelope_id = envelope.id
                            WHERE envelope.resource_type = 'integration_event'
                              AND envelope.resource_id IN (
                                  :feed_event_id, :webhook_failed_event_id
                              )
                            """
                        ),
                        {
                            "feed_event_id": feed_event_id,
                            "webhook_failed_event_id": webhook_failed_event_id,
                        },
                    ).all()
                )
                assert event_labels == {
                    feed_event_id: restricted_label_id,
                    webhook_failed_event_id: restricted_label_id,
                }
                assert connection.scalar(
                    text(
                        "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                    )
                ) == 0

            command.downgrade(config, "0069_data_policy_foundation")
            inspector.clear_cache()
            assert "data_access_envelopes" not in inspector.get_table_names(
                schema=schema_name
            )
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT handling_label_id FROM feeds WHERE id = :id"),
                    {"id": old_process_feed_id},
                ) == UNRESTRICTED_HANDLING_LABEL_ID
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
