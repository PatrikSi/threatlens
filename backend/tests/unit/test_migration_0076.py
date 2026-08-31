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
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.services.integration_metric_data_policy import (
    integration_metric_policy_cohort_key,
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


def test_integration_metric_cohort_migration_is_guarded_and_downgrade_safe(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0076_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    integration_id = uuid.uuid4()
    legacy_metric_id = uuid.uuid4()
    classified_metric_id = uuid.uuid4()
    classified_cohort_id = uuid.uuid4()
    restricted_label_id = uuid.uuid4()
    feed_id = uuid.uuid4()

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
            command.upgrade(config, "0075_notification_lineage_repair")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO integration_instances "
                        "(id, name, integration_type, direction, config_json) "
                        "VALUES (:id, 'Migration SMTP', 'smtp', "
                        "'destination', '{}'::json)"
                    ),
                    {"id": integration_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metrics "
                        "(id, bucket_start, integration_id, connector_type, "
                        "event_type, succeeded_count, failed_count, "
                        "dead_letter_count, attempt_count, duration_total_ms, "
                        "duration_max_ms) VALUES "
                        "(:id, '2026-08-01T10:00:00Z', :integration_id, "
                        "'smtp', 'rss_item_new', 2, 1, 3, 7, 900, 400)"
                    ),
                    {"id": legacy_metric_id, "integration_id": integration_id},
                )
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0076_integration_metric_cohorts")
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
                command.upgrade(config, "0076_integration_metric_cohorts")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1")
                )
                policy_revision = int(
                    connection.scalar(
                        text("SELECT revision FROM data_policy_state WHERE id = 1")
                    )
                )

            command.upgrade(config, "0076_integration_metric_cohorts")
            with schema_engine.connect() as connection:
                cohort = connection.execute(
                    text(
                        "SELECT id, policy_cohort_key, captured_policy_revision, "
                        "provenance_complete, source_count, succeeded_count, "
                        "failed_count, dead_letter_count, attempt_count, "
                        "duration_total_ms, duration_max_ms "
                        "FROM integration_delivery_metric_cohorts "
                        "WHERE metric_id = :metric_id"
                    ),
                    {"metric_id": legacy_metric_id},
                ).one()
                assert cohort.policy_cohort_key == (
                    integration_metric_policy_cohort_key(
                        policy_revision=policy_revision,
                        provenance_complete=False,
                        source_count=0,
                        label_ids={QUARANTINE_HANDLING_LABEL_ID},
                        feed_ids=set(),
                    )
                )
                assert cohort.captured_policy_revision == policy_revision
                assert cohort.provenance_complete is False
                assert cohort.source_count == 0
                assert cohort[5:] == (2, 1, 3, 7, 900, 400)
                assert set(
                    connection.scalars(
                        text(
                            "SELECT label_id "
                            "FROM integration_delivery_metric_cohort_labels "
                            "WHERE cohort_id = :cohort_id"
                        ),
                        {"cohort_id": cohort.id},
                    ).all()
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert connection.scalar(
                    text(
                        "SELECT count(*) "
                        "FROM integration_delivery_metric_cohort_feeds "
                        "WHERE cohort_id = :cohort_id"
                    ),
                    {"cohort_id": cohort.id},
                ) == 0

            with schema_engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(DBAPIError, match="policy-cohort-aware"):
                    connection.execute(
                        text(
                            "UPDATE integration_delivery_metrics "
                            "SET succeeded_count = succeeded_count + 1 "
                            "WHERE id = :id"
                        ),
                        {"id": legacy_metric_id},
                    )
                transaction.rollback()

            command.downgrade(config, "0075_notification_lineage_repair")
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text(
                        "SELECT succeeded_count "
                        "FROM integration_delivery_metrics WHERE id = :id"
                    ),
                    {"id": legacy_metric_id},
                ) == 2
            command.upgrade(config, "0076_integration_metric_cohorts")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO handling_labels "
                        "(id, key, name, color) VALUES "
                        "(:id, 'migration-0076-restricted', "
                        "'Migration 0076 restricted', '#B91C1C')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Migration 0076 feed', 'encrypted', :digest, :label)"
                    ),
                    {
                        "id": feed_id,
                        "digest": "7" * 64,
                        "label": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'threatlens.integration_metric_cohort_write', 'on', true)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metrics "
                        "(id, bucket_start, integration_id, connector_type, "
                        "event_type, succeeded_count, failed_count, "
                        "dead_letter_count, attempt_count, duration_total_ms, "
                        "duration_max_ms) VALUES "
                        "(:id, '2026-08-02T10:00:00Z', :integration_id, "
                        "'smtp', 'alert_match', 4, 0, 0, 4, 500, 200)"
                    ),
                    {
                        "id": classified_metric_id,
                        "integration_id": integration_id,
                    },
                )
                cohort_key = integration_metric_policy_cohort_key(
                    policy_revision=policy_revision,
                    provenance_complete=True,
                    source_count=1,
                    label_ids={restricted_label_id},
                    feed_ids={feed_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metric_cohorts "
                        "(id, metric_id, policy_cohort_key, "
                        "captured_policy_revision, provenance_complete, "
                        "source_count, succeeded_count, failed_count, "
                        "dead_letter_count, attempt_count, duration_total_ms, "
                        "duration_max_ms) VALUES "
                        "(:id, :metric_id, :cohort_key, :revision, true, 1, "
                        "4, 0, 0, 4, 500, 200)"
                    ),
                    {
                        "id": classified_cohort_id,
                        "metric_id": classified_metric_id,
                        "cohort_key": cohort_key,
                        "revision": policy_revision,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metric_cohort_labels "
                        "(cohort_id, label_id) VALUES (:cohort_id, :label_id)"
                    ),
                    {
                        "cohort_id": classified_cohort_id,
                        "label_id": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metric_cohort_feeds "
                        "(cohort_id, source_feed_id_snapshot) "
                        "VALUES (:cohort_id, :feed_id)"
                    ),
                    {"cohort_id": classified_cohort_id, "feed_id": feed_id},
                )
                connection.execute(
                    text(
                        "UPDATE feeds SET handling_label_id = :label_id "
                        "WHERE id = :feed_id"
                    ),
                    {
                        "feed_id": feed_id,
                        "label_id": UNRESTRICTED_HANDLING_LABEL_ID,
                    },
                )
                assert set(
                    connection.scalars(
                        text(
                            "SELECT label_id "
                            "FROM integration_delivery_metric_cohort_labels "
                            "WHERE cohort_id = :cohort_id"
                        ),
                        {"cohort_id": classified_cohort_id},
                    ).all()
                ) == {restricted_label_id, UNRESTRICTED_HANDLING_LABEL_ID}
                connection.execute(
                    text("DELETE FROM feeds WHERE id = :feed_id"),
                    {"feed_id": feed_id},
                )
                assert connection.scalar(
                    text(
                        "SELECT source_feed_id_snapshot "
                        "FROM integration_delivery_metric_cohort_feeds "
                        "WHERE cohort_id = :cohort_id"
                    ),
                    {"cohort_id": classified_cohort_id},
                ) == feed_id

            with schema_engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(DBAPIError, match="historical integration metrics"):
                    connection.execute(
                        text(
                            "UPDATE handling_labels SET is_active = false "
                            "WHERE id = :id"
                        ),
                        {"id": restricted_label_id},
                    )
                transaction.rollback()

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0075_notification_lineage_repair")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="classified rollups exist"):
                command.downgrade(config, "0075_notification_lineage_repair")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM integration_delivery_metrics WHERE id = :id"
                    ),
                    {"id": classified_metric_id},
                )
            command.downgrade(config, "0075_notification_lineage_repair")
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0075_notification_lineage_repair"
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
