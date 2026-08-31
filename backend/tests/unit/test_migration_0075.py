from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
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
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


def test_notification_lineage_repair_quarantines_descendants_and_is_downgrade_safe(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0075_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    restricted_label_id = uuid.uuid4()
    item_feed_id = uuid.uuid4()
    misleading_feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    user_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    legacy_delivery_id = uuid.uuid4()
    missing_item_legacy_id = uuid.uuid4()
    event_id = uuid.uuid4()
    missing_item_event_id = uuid.uuid4()
    unaffected_event_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    retry_delivery_id = uuid.uuid4()
    standalone_delivery_id = uuid.uuid4()
    standalone_retry_delivery_id = uuid.uuid4()

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
            command.upgrade(config, "0069_data_policy_foundation")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO handling_labels (id, key, name, color) "
                        "VALUES (:id, 'migration-0075-restricted', "
                        "'Migration 0075 restricted', '#B91C1C')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:item_feed_id, 'Restricted item feed', 'encrypted', "
                        ":item_digest, :restricted_label), "
                        "(:misleading_feed_id, 'Misleading unrestricted feed', "
                        "'encrypted', :misleading_digest, :unrestricted_label)"
                    ),
                    {
                        "item_feed_id": item_feed_id,
                        "item_digest": "1" * 64,
                        "restricted_label": restricted_label_id,
                        "misleading_feed_id": misleading_feed_id,
                        "misleading_digest": "2" * 64,
                        "unrestricted_label": UNRESTRICTED_HANDLING_LABEL_ID,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash, status) "
                        "VALUES (:id, :feed_id, 'https://example.com/item', "
                        "'Restricted item', 'migration-0075-item', :digest, "
                        "'content_fetched')"
                    ),
                    {"id": item_id, "feed_id": item_feed_id, "digest": "a" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_active, is_approved) VALUES "
                        "(:id, 'migration-0075@example.com', 'hash', true, true)"
                    ),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_instances "
                        "(id, owner_user_id, name, integration_type, direction, "
                        "enabled, config_json) VALUES "
                        "(:id, :user_id, 'Migration webhook integration', "
                        "'webhook', 'destination', true, '{}'::json)"
                    ),
                    {"id": integration_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_subscriptions "
                        "(id, integration_id, subscription_key, event_type, enabled) "
                        "VALUES (:id, :integration_id, 'event:webhook_failed', "
                        "'webhook_failed', true)"
                    ),
                    {"id": subscription_id, "integration_id": integration_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhooks "
                        "(id, user_id, name, event_type, url_template, method, "
                        "feed_scope, feed_ids_json, query_params_json, headers_json, "
                        "body_mode, body_fields_json) VALUES "
                        "(:id, :user_id, 'Migration webhook', 'rss_item_new', "
                        "'https://example.com/hook', 'POST', 'all', '[]'::json, "
                        "'[]'::json, '[]'::json, 'none', '[]'::json)"
                    ),
                    {"id": webhook_id, "user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO notification_webhook_deliveries "
                        "(id, webhook_id, user_id, event_type_snapshot, item_id, "
                        "feed_id, delivery_state, success, rendered_url, "
                        "rendered_method, rendered_headers_json, "
                        "rendered_query_params_json) VALUES "
                        "(:id, :webhook_id, :user_id, 'rss_item_new', :item_id, "
                        ":feed_id, 'failed', false, 'https://example.com/hook', "
                        "'POST', '[]'::json, '[]'::json), "
                        "(:missing_id, :webhook_id, :user_id, 'rss_item_new', "
                        "NULL, :feed_id, 'failed', false, "
                        "'https://example.com/hook', 'POST', '[]'::json, "
                        "'[]'::json)"
                    ),
                    {
                        "id": legacy_delivery_id,
                        "webhook_id": webhook_id,
                        "user_id": user_id,
                        "item_id": item_id,
                        "feed_id": misleading_feed_id,
                        "missing_id": missing_item_legacy_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_events "
                        "(id, event_type, source_type, source_id, actor_user_id, "
                        "idempotency_key, payload_json) VALUES "
                        "(:event_id, 'webhook_failed', "
                        "'notification_webhook_delivery', :legacy_id, :user_id, "
                        ":event_key, '{}'::json), "
                        "(:missing_event_id, 'webhook_failed', "
                        "'notification_webhook_delivery', :missing_legacy_id, "
                        ":user_id, :missing_event_key, '{}'::json), "
                        "(:unaffected_id, 'feed_failing', 'feed', :feed_id, "
                        ":user_id, :unaffected_key, '{}'::json)"
                    ),
                    {
                        "event_id": event_id,
                        "legacy_id": str(legacy_delivery_id),
                        "event_key": f"migration-0075-event:{event_id}",
                        "missing_event_id": missing_item_event_id,
                        "missing_legacy_id": str(missing_item_legacy_id),
                        "missing_event_key": (
                            f"migration-0075-missing:{missing_item_event_id}"
                        ),
                        "unaffected_id": unaffected_event_id,
                        "feed_id": str(misleading_feed_id),
                        "unaffected_key": (
                            f"migration-0075-unaffected:{unaffected_event_id}"
                        ),
                        "user_id": user_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_deliveries "
                        "(id, integration_id, subscription_id, event_id, "
                        "owner_user_id, connector_type, event_type, delivery_kind, "
                        "state, idempotency_key, payload_json) VALUES "
                        "(:delivery_id, :integration_id, :subscription_id, "
                        ":event_id, :user_id, 'webhook', 'webhook_failed', 'live', "
                        "'failed', :delivery_key, '{}'::json), "
                        "(:retry_id, :integration_id, :subscription_id, NULL, "
                        ":user_id, 'webhook', 'webhook_failed', 'retry', 'failed', "
                        ":retry_key, '{}'::json), "
                        "(:standalone_id, :integration_id, :subscription_id, NULL, "
                        ":user_id, 'webhook', 'webhook_failed', 'live', 'failed', "
                        ":standalone_key, json_build_object("
                        "'legacy_webhook_delivery_id', CAST(:legacy_id AS text))), "
                        "(:standalone_retry_id, :integration_id, :subscription_id, "
                        "NULL, :user_id, 'webhook', 'webhook_failed', 'retry', "
                        "'failed', :standalone_retry_key, '{}'::json)"
                    ),
                    {
                        "delivery_id": delivery_id,
                        "retry_id": retry_delivery_id,
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                        "integration_id": integration_id,
                        "subscription_id": subscription_id,
                        "event_id": event_id,
                        "user_id": user_id,
                        "delivery_key": f"migration-0075-delivery:{delivery_id}",
                        "retry_key": f"migration-0075-retry:{retry_delivery_id}",
                        "standalone_key": (
                            f"migration-0075-standalone:{standalone_delivery_id}"
                        ),
                        "standalone_retry_key": (
                            "migration-0075-standalone-retry:"
                            f"{standalone_retry_delivery_id}"
                        ),
                        "legacy_id": str(legacy_delivery_id),
                    },
                )
                connection.execute(
                    text(
                        "UPDATE integration_deliveries SET source_delivery_id = :source "
                        "WHERE id = :retry"
                    ),
                    {"source": delivery_id, "retry": retry_delivery_id},
                )
                connection.execute(
                    text(
                        "UPDATE integration_deliveries SET source_delivery_id = :source "
                        "WHERE id = :retry"
                    ),
                    {
                        "source": standalone_delivery_id,
                        "retry": standalone_retry_delivery_id,
                    },
                )

            command.upgrade(config, "0074_ai_provider_receipts")

            with schema_engine.begin() as connection:
                # Emulate the pre-fix runtime path: it classified standalone
                # legacy deliveries by the item's real feed while ignoring the
                # contradictory feed snapshot, then returned that envelope on
                # every later read.
                connection.execute(
                    text(
                        "UPDATE data_access_envelope_sources AS source SET "
                        "source_type = 'item', source_id = :item_id, "
                        "source_feed_id = :feed_id, handling_label_id = :label_id, "
                        "source_digest = :digest "
                        "FROM data_access_envelopes AS envelope "
                        "WHERE source.envelope_id = envelope.id "
                        "AND envelope.resource_type = 'integration_delivery' "
                        "AND envelope.resource_id IN "
                        "(:standalone_id, :standalone_retry_id)"
                    ),
                    {
                        "item_id": str(item_id),
                        "feed_id": item_feed_id,
                        "label_id": restricted_label_id,
                        "digest": "a" * 64,
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                )
                connection.execute(
                    text(
                        "DELETE FROM data_access_envelope_labels AS label "
                        "USING data_access_envelopes AS envelope "
                        "WHERE label.envelope_id = envelope.id "
                        "AND envelope.resource_type = 'integration_delivery' "
                        "AND envelope.resource_id IN "
                        "(:standalone_id, :standalone_retry_id)"
                    ),
                    {
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) "
                        "SELECT source.envelope_id, source.handling_label_id, "
                        "count(*)::integer "
                        "FROM data_access_envelope_sources AS source "
                        "JOIN data_access_envelopes AS envelope "
                        "ON envelope.id = source.envelope_id "
                        "WHERE envelope.resource_type = 'integration_delivery' "
                        "AND envelope.resource_id IN "
                        "(:standalone_id, :standalone_retry_id) "
                        "GROUP BY source.envelope_id, source.handling_label_id"
                    ),
                    {
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                )
                unsafe_rows = connection.execute(
                    text(
                        "SELECT envelope.resource_id, source.source_type, "
                        "source.source_feed_id, source.handling_label_id "
                        "FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_sources AS source "
                        "ON source.envelope_id = envelope.id "
                        "WHERE envelope.resource_id IN "
                        "(:event_id, :missing_event_id, :delivery_id, :retry_id, "
                        ":standalone_id, :standalone_retry_id)"
                    ),
                    {
                        "event_id": event_id,
                        "missing_event_id": missing_item_event_id,
                        "delivery_id": delivery_id,
                        "retry_id": retry_delivery_id,
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                ).all()
                assert len(unsafe_rows) == 6
                assert {
                    (
                        row.resource_id,
                        row.source_type,
                        row.source_feed_id,
                        row.handling_label_id,
                    )
                    for row in unsafe_rows
                } == {
                    (
                        resource_id,
                        "item",
                        misleading_feed_id,
                        UNRESTRICTED_HANDLING_LABEL_ID,
                    )
                    for resource_id in (event_id, delivery_id, retry_delivery_id)
                } | {
                    (
                        resource_id,
                        "item",
                        item_feed_id,
                        restricted_label_id,
                    )
                    for resource_id in (
                        standalone_delivery_id,
                        standalone_retry_delivery_id,
                    )
                } | {
                    (
                        missing_item_event_id,
                        "feed",
                        misleading_feed_id,
                        UNRESTRICTED_HANDLING_LABEL_ID,
                    )
                }
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0074_ai_provider_receipts"
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1")
                )

            command.upgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                policy_revision = connection.scalar(
                    text("SELECT revision FROM data_policy_state WHERE id = 1")
                )
                repaired_rows = connection.execute(
                    text(
                        "SELECT envelope.resource_id, source.id, source.source_type, "
                        "source.source_id, source.source_version, "
                        "source.source_feed_id, source.source_parent_id, "
                        "source.handling_label_id, "
                        "source.captured_policy_revision, source.source_digest "
                        "FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_sources AS source "
                        "ON source.envelope_id = envelope.id "
                        "WHERE envelope.resource_id IN "
                        "(:event_id, :missing_event_id, :delivery_id, :retry_id, "
                        ":standalone_id, :standalone_retry_id)"
                    ),
                    {
                        "event_id": event_id,
                        "missing_event_id": missing_item_event_id,
                        "delivery_id": delivery_id,
                        "retry_id": retry_delivery_id,
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                ).all()
                repaired = {row.resource_id: row for row in repaired_rows}
                assert set(repaired) == {
                    event_id,
                    missing_item_event_id,
                    delivery_id,
                    retry_delivery_id,
                    standalone_delivery_id,
                    standalone_retry_delivery_id,
                }
                for root_id, child_ids, expected_source_id in (
                    (
                        event_id,
                        (delivery_id, retry_delivery_id),
                        str(item_id),
                    ),
                    (missing_item_event_id, (), str(misleading_feed_id)),
                    (
                        standalone_delivery_id,
                        (standalone_retry_delivery_id,),
                        str(item_id),
                    ),
                ):
                    root = repaired[root_id]
                    assert root.source_parent_id is None
                    parent = root
                    for child_id in child_ids:
                        child = repaired[child_id]
                        assert child.source_parent_id == parent.id
                        parent = child
                    assert {
                        (
                            row.source_type,
                            row.source_id,
                            row.source_version,
                            row.source_feed_id,
                            row.handling_label_id,
                            row.captured_policy_revision,
                            row.source_digest,
                        )
                        for row in (root, *(repaired[value] for value in child_ids))
                    } == {
                        (
                            "unresolved",
                            expected_source_id,
                            f"repair:0075:{root.id}",
                            None,
                            QUARANTINE_HANDLING_LABEL_ID,
                            policy_revision,
                            None,
                        )
                    }
                aggregates = connection.execute(
                    text(
                        "SELECT envelope.resource_id, "
                        "envelope.source_count AS envelope_source_count, "
                        "envelope.policy_revision, label.label_id, "
                        "label.source_count AS label_source_count "
                        "FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_labels AS label "
                        "ON label.envelope_id = envelope.id "
                        "WHERE envelope.resource_id IN "
                        "(:event_id, :missing_event_id, :delivery_id, :retry_id, "
                        ":standalone_id, :standalone_retry_id)"
                    ),
                    {
                        "event_id": event_id,
                        "missing_event_id": missing_item_event_id,
                        "delivery_id": delivery_id,
                        "retry_id": retry_delivery_id,
                        "standalone_id": standalone_delivery_id,
                        "standalone_retry_id": standalone_retry_delivery_id,
                    },
                ).all()
                assert {
                    (
                        row.envelope_source_count,
                        row.policy_revision,
                        row.label_id,
                        row.label_source_count,
                    )
                    for row in aggregates
                } == {
                    (1, policy_revision, QUARANTINE_HANDLING_LABEL_ID, 1)
                }
                unaffected = connection.execute(
                    text(
                        "SELECT source.source_type, source.source_feed_id, "
                        "source.handling_label_id "
                        "FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_sources AS source "
                        "ON source.envelope_id = envelope.id "
                        "WHERE envelope.resource_type = 'integration_event' "
                        "AND envelope.resource_id = :event_id"
                    ),
                    {"event_id": unaffected_event_id},
                ).one()
                assert unaffected == (
                    "feed",
                    misleading_feed_id,
                    UNRESTRICTED_HANDLING_LABEL_ID,
                )
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": legacy_delivery_id},
                ).one() == (item_id, item_feed_id)
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": missing_item_legacy_id},
                ).one() == (None, None)
                repaired_ids = {row.resource_id: row.id for row in repaired_rows}
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0074_ai_provider_receipts")
            with schema_engine.begin() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0075_notification_lineage_repair"
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0074_ai_provider_receipts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1")
                )

            command.downgrade(config, "0074_ai_provider_receipts")
            command.upgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                persisted = dict(
                    connection.execute(
                        text(
                            "SELECT envelope.resource_id, source.id "
                            "FROM data_access_envelopes AS envelope "
                            "JOIN data_access_envelope_sources AS source "
                            "ON source.envelope_id = envelope.id "
                            "WHERE envelope.resource_id IN "
                            "(:event_id, :missing_event_id, :delivery_id, "
                            ":retry_id, :standalone_id, :standalone_retry_id)"
                        ),
                        {
                            "event_id": event_id,
                            "missing_event_id": missing_item_event_id,
                            "delivery_id": delivery_id,
                            "retry_id": retry_delivery_id,
                            "standalone_id": standalone_delivery_id,
                            "standalone_retry_id": standalone_retry_delivery_id,
                        },
                    ).all()
                )
                assert persisted == repaired_ids
                assert set(
                    connection.scalars(
                        text(
                            "SELECT DISTINCT label.label_id "
                            "FROM data_access_envelopes AS envelope "
                            "JOIN data_access_envelope_labels AS label "
                            "ON label.envelope_id = envelope.id "
                            "WHERE envelope.resource_id IN "
                            "(:event_id, :missing_event_id, :delivery_id, "
                            ":retry_id, :standalone_id, :standalone_retry_id)"
                        ),
                        {
                            "event_id": event_id,
                            "missing_event_id": missing_item_event_id,
                            "delivery_id": delivery_id,
                            "retry_id": retry_delivery_id,
                            "standalone_id": standalone_delivery_id,
                            "standalone_retry_id": standalone_retry_delivery_id,
                        },
                    ).all()
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": legacy_delivery_id},
                ).one() == (item_id, item_feed_id)
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": missing_item_legacy_id},
                ).one() == (None, None)

                connection.execute(
                    text("DELETE FROM items WHERE id = :id"),
                    {"id": item_id},
                )
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": legacy_delivery_id},
                ).one() == (None, item_feed_id)
                connection.execute(
                    text("DELETE FROM feeds WHERE id = :id"),
                    {"id": item_feed_id},
                )
                assert connection.execute(
                    text(
                        "SELECT item_id, feed_id "
                        "FROM notification_webhook_deliveries WHERE id = :id"
                    ),
                    {"id": legacy_delivery_id},
                ).one() == (None, None)
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
