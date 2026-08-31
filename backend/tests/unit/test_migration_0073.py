from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.services.alert_metric_data_policy import alert_metric_policy_cohort_key


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


def test_alert_metric_policy_upgrade_and_downgrade_preserve_counts(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0073_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    owner_id = uuid.uuid4()
    legacy_metric_id = uuid.uuid4()
    unrestricted_metric_id = uuid.uuid4()
    unrestricted_cohort_id = uuid.uuid4()

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
            command.upgrade(config, "0072_report_ready_owner_envelope")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_active, is_approved) "
                        "VALUES (:id, 'migration-0073@example.com', 'hash', true, true)"
                    ),
                    {"id": owner_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metrics "
                        "(id, bucket_start, owner_user_id, severity, lifecycle_state, "
                        "suppressed, occurrence_count) VALUES "
                        "(:id, '2026-08-01T00:00:00Z', :owner_id, 'high', "
                        "'closed', false, 2)"
                    ),
                    {"id": legacy_metric_id, "owner_id": owner_id},
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="before the rolling upgrade"):
                command.upgrade(config, "0073_alert_metric_data_policy")
            assert "alert_occurrence_metric_cohorts" not in set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'disabled' WHERE id = 1")
                )
            command.upgrade(config, "0073_alert_metric_data_policy")
            table_names = set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )
            assert "alert_occurrence_metric_cohorts" in table_names
            assert "alert_occurrence_metric_cohort_labels" in table_names
            with schema_engine.begin() as connection:
                assert connection.scalar(
                    text(
                        "SELECT source_feed_id_snapshot "
                        "FROM alert_occurrence_metric_cohorts "
                        "WHERE metric_id = :id"
                    ),
                    {"id": legacy_metric_id},
                ) == uuid.UUID(int=0)
                assert (
                    connection.scalar(
                        text(
                            "SELECT label.label_id "
                            "FROM alert_occurrence_metric_cohort_labels AS label "
                            "JOIN alert_occurrence_metric_cohorts AS cohort "
                            "ON cohort.id = label.cohort_id "
                            "WHERE cohort.metric_id = :id"
                        ),
                        {"id": legacy_metric_id},
                    )
                    == QUARANTINE_HANDLING_LABEL_ID
                )
                assert connection.scalar(
                    text(
                        "SELECT policy_cohort_key "
                        "FROM alert_occurrence_metric_cohorts "
                        "WHERE metric_id = :id"
                    ),
                    {"id": legacy_metric_id},
                ) == alert_metric_policy_cohort_key(
                    policy_revision=0,
                    label_ids={QUARANTINE_HANDLING_LABEL_ID},
                )
            with schema_engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(DBAPIError):
                    connection.execute(
                        text(
                            "INSERT INTO alert_occurrence_metrics "
                            "(id, bucket_start, owner_user_id, severity, "
                            "lifecycle_state, suppressed, occurrence_count) VALUES "
                            "(:id, '2026-08-02T00:00:00Z', :owner_id, 'high', "
                            "'closed', false, 1)"
                        ),
                        {"id": uuid.uuid4(), "owner_id": owner_id},
                    )
                transaction.rollback()
            source_feed_id = uuid.uuid4()
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) "
                        "VALUES (:id, 'Migration 0073 feed', :url, :url_digest, "
                        ":label_id)"
                    ),
                    {
                        "id": source_feed_id,
                        "url": f"https://example.com/{source_feed_id}.xml",
                        "url_digest": uuid.uuid4().hex.ljust(64, "0"),
                        "label_id": QUARANTINE_HANDLING_LABEL_ID,
                    },
                )
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'threatlens.alert_metric_cohort_write', 'on', true)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metrics "
                        "(id, bucket_start, owner_user_id, severity, "
                        "lifecycle_state, suppressed, occurrence_count) "
                        "VALUES (:id, '2026-08-02T00:00:00Z', :owner_id, 'high', "
                        "'closed', false, 3)"
                    ),
                    {
                        "id": unrestricted_metric_id,
                        "owner_id": owner_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metric_cohorts "
                        "(id, metric_id, source_feed_id_snapshot, policy_cohort_key, "
                        "occurrence_count) VALUES "
                        "(:id, :metric_id, :source_feed_id, :policy_cohort_key, 3)"
                    ),
                    {
                        "id": unrestricted_cohort_id,
                        "metric_id": unrestricted_metric_id,
                        "source_feed_id": source_feed_id,
                        "policy_cohort_key": alert_metric_policy_cohort_key(
                            policy_revision=1,
                            label_ids={QUARANTINE_HANDLING_LABEL_ID},
                        ),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metric_cohort_labels "
                        "(cohort_id, label_id) VALUES (:id, :label_id)"
                    ),
                    {
                        "id": unrestricted_cohort_id,
                        "label_id": QUARANTINE_HANDLING_LABEL_ID,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE feeds SET handling_label_id = :label_id "
                        "WHERE id = :feed_id"
                    ),
                    {
                        "feed_id": source_feed_id,
                        "label_id": UNRESTRICTED_HANDLING_LABEL_ID,
                    },
                )
                assert set(
                    connection.scalars(
                        text(
                            "SELECT label_id "
                            "FROM alert_occurrence_metric_cohort_labels "
                            "WHERE cohort_id = :cohort_id"
                        ),
                        {"cohort_id": unrestricted_cohort_id},
                    ).all()
                ) == {
                    QUARANTINE_HANDLING_LABEL_ID,
                    UNRESTRICTED_HANDLING_LABEL_ID,
                }

            with schema_engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "UPDATE handling_labels SET is_active = false "
                            "WHERE id = :label_id"
                        ),
                        {"label_id": QUARANTINE_HANDLING_LABEL_ID},
                    )
                transaction.rollback()

            with pytest.raises(RuntimeError, match="classified metric rollups"):
                command.downgrade(config, "0072_report_ready_owner_envelope")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM alert_occurrence_metrics WHERE id = :id"),
                    {"id": unrestricted_metric_id},
                )
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="audit or enforcement"):
                command.downgrade(config, "0072_report_ready_owner_envelope")
            with schema_engine.begin() as connection:
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'disabled' WHERE id = 1")
                )
            command.downgrade(config, "0072_report_ready_owner_envelope")
            downgraded_columns = {
                column["name"]
                for column in inspect(schema_engine).get_columns(
                    "alert_occurrence_metrics",
                    schema=schema_name,
                )
            }
            assert "source_feed_id_snapshot" not in downgraded_columns
            downgraded_tables = set(
                inspect(schema_engine).get_table_names(schema=schema_name)
            )
            assert "alert_occurrence_metric_cohorts" not in downgraded_tables
            assert "alert_occurrence_metric_cohort_labels" not in downgraded_tables
            with schema_engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT occurrence_count FROM alert_occurrence_metrics "
                        "WHERE owner_user_id = :owner_id"
                    ),
                    {"owner_id": owner_id},
                ).all()
                assert [row.occurrence_count for row in rows] == [2]

            command.upgrade(config, "0073_alert_metric_data_policy")
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT label.label_id "
                            "FROM alert_occurrence_metric_cohort_labels AS label "
                            "JOIN alert_occurrence_metric_cohorts AS cohort "
                            "ON cohort.id = label.cohort_id "
                            "JOIN alert_occurrence_metrics AS metric "
                            "ON metric.id = cohort.metric_id "
                            "WHERE metric.owner_user_id = :owner_id"
                        ),
                        {"owner_id": owner_id},
                    )
                    == QUARANTINE_HANDLING_LABEL_ID
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
