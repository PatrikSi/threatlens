from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID
from app.services.alert_metric_data_policy import alert_metric_policy_cohort_key
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


def test_metric_captured_taint_upgrade_repairs_and_downgrades_safely(
    test_database_url,
    monkeypatch,
):
    schema_name = f"migration_0079_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)
    owner_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    captured_label_id = uuid.uuid4()
    taint_label_id = uuid.uuid4()
    extra_label_id = uuid.uuid4()
    ambiguous_label_ids = [uuid.uuid4() for _ in range(13)]
    alert_metric_id = uuid.uuid4()
    alert_cohort_id = uuid.uuid4()
    compatibility_metric_id = uuid.uuid4()
    compatibility_cohort_id = uuid.uuid4()
    ambiguous_metric_id = uuid.uuid4()
    ambiguous_cohort_id = uuid.uuid4()
    integration_metric_id = uuid.uuid4()
    integration_cohort_id = uuid.uuid4()
    ambiguous_integration_metric_id = uuid.uuid4()
    ambiguous_integration_cohort_id = uuid.uuid4()
    policy_revision = 500_000
    captured_revision = 3

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
            command.upgrade(config, "0078_ai_telemetry_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET revision = :revision, "
                        "coverage_version = 0 WHERE id = 1"
                    ),
                    {"revision": policy_revision},
                )
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, password_hash, is_active, is_approved) "
                        "VALUES (:id, :email, 'hash', true, true)"
                    ),
                    {"id": owner_id, "email": f"migration-0079-{owner_id}@test"},
                )
                _insert_labels(
                    connection,
                    [
                        captured_label_id,
                        taint_label_id,
                        extra_label_id,
                        *ambiguous_label_ids,
                    ],
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Migration 0079 feed', 'encrypted', :digest, :label)"
                    ),
                    {
                        "id": feed_id,
                        "digest": "9" * 64,
                        "label": taint_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_instances "
                        "(id, name, integration_type, direction, config_json) "
                        "VALUES (:id, 'Migration 0079 SMTP', 'smtp', "
                        "'destination', '{}'::json)"
                    ),
                    {"id": integration_id},
                )
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'threatlens.alert_metric_cohort_write', 'on', true)"
                    )
                )
                _insert_alert_metric(
                    connection,
                    metric_id=alert_metric_id,
                    owner_id=owner_id,
                    bucket="2026-08-01T00:00:00Z",
                    severity="high",
                    count=2,
                )
                _insert_alert_metric(
                    connection,
                    metric_id=ambiguous_metric_id,
                    owner_id=owner_id,
                    bucket="2026-08-02T00:00:00Z",
                    severity="critical",
                    count=4,
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metric_cohorts "
                        "(id, metric_id, source_feed_id_snapshot, "
                        "policy_cohort_key, occurrence_count) VALUES "
                        "(:id, :metric_id, :feed_id, :cohort_key, 2), "
                        "(:ambiguous_id, :ambiguous_metric_id, :feed_id, "
                        ":ambiguous_key, 4)"
                    ),
                    {
                        "id": alert_cohort_id,
                        "metric_id": alert_metric_id,
                        "feed_id": feed_id,
                        "cohort_key": alert_metric_policy_cohort_key(
                            policy_revision=captured_revision,
                            label_ids={captured_label_id},
                        ),
                        "ambiguous_id": ambiguous_cohort_id,
                        "ambiguous_metric_id": ambiguous_metric_id,
                        "ambiguous_key": "f" * 64,
                    },
                )
                _insert_effective_labels(
                    connection,
                    table="alert_occurrence_metric_cohort_labels",
                    cohort_id=alert_cohort_id,
                    label_ids={captured_label_id, taint_label_id},
                )
                _insert_effective_labels(
                    connection,
                    table="alert_occurrence_metric_cohort_labels",
                    cohort_id=ambiguous_cohort_id,
                    label_ids=set(ambiguous_label_ids),
                )
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'threatlens.integration_metric_cohort_write', "
                        "'on', true)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metrics "
                        "(id, bucket_start, integration_id, connector_type, "
                        "event_type, succeeded_count, failed_count, "
                        "dead_letter_count, attempt_count, duration_total_ms, "
                        "duration_max_ms) VALUES "
                        "(:id, '2026-08-01T10:00:00Z', :integration_id, "
                        "'smtp', 'rss_item_new', 2, 1, 0, 3, 300, 150), "
                        "(:ambiguous_id, '2026-08-02T10:00:00Z', "
                        ":integration_id, 'smtp', 'alert_match', "
                        "0, 2, 0, 2, 220, 110)"
                    ),
                    {
                        "id": integration_metric_id,
                        "ambiguous_id": ambiguous_integration_metric_id,
                        "integration_id": integration_id,
                    },
                )
                integration_key = integration_metric_policy_cohort_key(
                    policy_revision=captured_revision,
                    provenance_complete=True,
                    source_count=1,
                    label_ids={captured_label_id},
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
                        "2, 1, 0, 3, 300, 150), "
                        "(:ambiguous_id, :ambiguous_metric_id, :ambiguous_key, "
                        ":revision, true, 2, 0, 2, 0, 2, 220, 110)"
                    ),
                    {
                        "id": integration_cohort_id,
                        "metric_id": integration_metric_id,
                        "cohort_key": integration_key,
                        "ambiguous_id": ambiguous_integration_cohort_id,
                        "ambiguous_metric_id": ambiguous_integration_metric_id,
                        "ambiguous_key": "e" * 64,
                        "revision": captured_revision,
                    },
                )
                _insert_effective_labels(
                    connection,
                    table="integration_delivery_metric_cohort_labels",
                    cohort_id=integration_cohort_id,
                    label_ids={captured_label_id, taint_label_id},
                )
                _insert_effective_labels(
                    connection,
                    table="integration_delivery_metric_cohort_labels",
                    cohort_id=ambiguous_integration_cohort_id,
                    label_ids={captured_label_id, taint_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metric_cohort_feeds "
                        "(cohort_id, source_feed_id_snapshot) VALUES "
                        "(:cohort_id, :feed_id), (:ambiguous_id, :feed_id)"
                    ),
                    {
                        "cohort_id": integration_cohort_id,
                        "ambiguous_id": ambiguous_integration_cohort_id,
                        "feed_id": feed_id,
                    },
                )
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )

            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0079_metric_captured_taint")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.upgrade(config, "0079_metric_captured_taint")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1"
                    )
                )
            command.upgrade(config, "0079_metric_captured_taint")

            with schema_engine.begin() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 0
                )
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_captured_labels",
                    alert_cohort_id,
                ) == {captured_label_id}
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_taint_labels",
                    alert_cohort_id,
                ) == {taint_label_id}
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_captured_labels",
                    ambiguous_cohort_id,
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_taint_labels",
                    ambiguous_cohort_id,
                ) == set(ambiguous_label_ids)
                ambiguous = connection.execute(
                    text(
                        "SELECT policy_cohort_key, captured_policy_revision, "
                        "provenance_complete, occurrence_count "
                        "FROM alert_occurrence_metric_cohorts WHERE id = :id"
                    ),
                    {"id": ambiguous_cohort_id},
                ).one()
                assert ambiguous.policy_cohort_key == alert_metric_policy_cohort_key(
                    policy_revision=policy_revision,
                    label_ids={QUARANTINE_HANDLING_LABEL_ID},
                )
                assert ambiguous[1:] == (policy_revision, False, 4)
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_captured_labels",
                    integration_cohort_id,
                ) == {captured_label_id}
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_taint_labels",
                    integration_cohort_id,
                ) == {taint_label_id}
                assert (
                    connection.scalar(
                        text(
                            "SELECT policy_cohort_key "
                            "FROM integration_delivery_metric_cohorts WHERE id = :id"
                        ),
                        {"id": integration_cohort_id},
                    )
                    == integration_key
                )
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_captured_labels",
                    ambiguous_integration_cohort_id,
                ) == {QUARANTINE_HANDLING_LABEL_ID}
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_taint_labels",
                    ambiguous_integration_cohort_id,
                ) == {captured_label_id, taint_label_id}
                ambiguous_integration = connection.execute(
                    text(
                        "SELECT policy_cohort_key, captured_policy_revision, "
                        "provenance_complete, source_count, failed_count "
                        "FROM integration_delivery_metric_cohorts WHERE id = :id"
                    ),
                    {"id": ambiguous_integration_cohort_id},
                ).one()
                assert ambiguous_integration.policy_cohort_key == (
                    integration_metric_policy_cohort_key(
                        policy_revision=policy_revision,
                        provenance_complete=False,
                        source_count=2,
                        label_ids={QUARANTINE_HANDLING_LABEL_ID},
                        feed_ids={feed_id},
                    )
                )
                assert ambiguous_integration[1:] == (
                    policy_revision,
                    False,
                    2,
                    2,
                )

                connection.execute(
                    text(
                        "SELECT set_config("
                        "'threatlens.alert_metric_cohort_write', 'on', true)"
                    )
                )
                _insert_alert_metric(
                    connection,
                    metric_id=compatibility_metric_id,
                    owner_id=owner_id,
                    bucket="2026-08-03T00:00:00Z",
                    severity="medium",
                    count=1,
                )
                compatibility_key = alert_metric_policy_cohort_key(
                    policy_revision=policy_revision,
                    label_ids={extra_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metric_cohorts "
                        "(id, metric_id, source_feed_id_snapshot, "
                        "policy_cohort_key, occurrence_count) VALUES "
                        "(:id, :metric_id, :feed_id, :cohort_key, 1)"
                    ),
                    {
                        "id": compatibility_cohort_id,
                        "metric_id": compatibility_metric_id,
                        "feed_id": feed_id,
                        "cohort_key": compatibility_key,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO alert_occurrence_metric_cohort_labels "
                        "(cohort_id, label_id) VALUES (:cohort_id, :label_id)"
                    ),
                    {
                        "cohort_id": compatibility_cohort_id,
                        "label_id": extra_label_id,
                    },
                )
                assert connection.execute(
                    text(
                        "SELECT captured_policy_revision, provenance_complete "
                        "FROM alert_occurrence_metric_cohorts WHERE id = :id"
                    ),
                    {"id": compatibility_cohort_id},
                ).one() == (policy_revision, True)
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_captured_labels",
                    compatibility_cohort_id,
                ) == {extra_label_id}

                with pytest.raises(DBAPIError, match="provenance is immutable"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "DELETE FROM "
                                "alert_occurrence_metric_cohort_taint_labels "
                                "WHERE cohort_id = :cohort_id"
                            ),
                            {"cohort_id": alert_cohort_id},
                        )
                with pytest.raises(DBAPIError, match="provenance is immutable"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE integration_delivery_metric_cohort_labels "
                                "SET label_id = :new_label WHERE cohort_id = :cohort_id "
                                "AND label_id = :old_label"
                            ),
                            {
                                "new_label": extra_label_id,
                                "cohort_id": integration_cohort_id,
                                "old_label": taint_label_id,
                            },
                        )

                connection.execute(
                    text("DELETE FROM alert_occurrence_metrics WHERE id = :id"),
                    {"id": ambiguous_metric_id},
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM "
                            "alert_occurrence_metric_cohort_captured_labels "
                            "WHERE cohort_id = :cohort_id"
                        ),
                        {"cohort_id": ambiguous_cohort_id},
                    )
                    == 0
                )

                connection.execute(
                    text(
                        "UPDATE feeds SET handling_label_id = :label_id "
                        "WHERE id = :feed_id"
                    ),
                    {"label_id": captured_label_id, "feed_id": feed_id},
                )
                assert _labels(
                    connection,
                    "alert_occurrence_metric_cohort_taint_labels",
                    alert_cohort_id,
                ) == {captured_label_id, taint_label_id}
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_taint_labels",
                    integration_cohort_id,
                ) == {captured_label_id, taint_label_id}
                assert _labels(
                    connection,
                    "integration_delivery_metric_cohort_labels",
                    integration_cohort_id,
                ) == {captured_label_id, taint_label_id}

                connection.execute(
                    text(
                        "INSERT INTO integration_delivery_metric_cohort_labels "
                        "(cohort_id, label_id) VALUES (:cohort_id, :label_id)"
                    ),
                    {"cohort_id": integration_cohort_id, "label_id": extra_label_id},
                )
                assert extra_label_id in _labels(
                    connection,
                    "integration_delivery_metric_cohort_taint_labels",
                    integration_cohort_id,
                )

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE integration_delivery_metric_cohorts "
                        "SET policy_cohort_key = :bad WHERE id = :id"
                    ),
                    {"bad": "0" * 64, "id": integration_cohort_id},
                )
            with pytest.raises(RuntimeError, match="parity validation"):
                command.downgrade(config, "0078_ai_telemetry_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE integration_delivery_metric_cohorts "
                        "SET policy_cohort_key = :key WHERE id = :id"
                    ),
                    {"key": integration_key, "id": integration_cohort_id},
                )
                connection.execute(
                    text("UPDATE data_policy_state SET mode = 'audit' WHERE id = 1")
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0078_ai_telemetry_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET mode = 'disabled', "
                        "coverage_version = 1 WHERE id = 1"
                    )
                )
            with pytest.raises(RuntimeError, match="Disable data policy first"):
                command.downgrade(config, "0078_ai_telemetry_policy")
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE data_policy_state SET coverage_version = 0 WHERE id = 1"
                    )
                )
            command.downgrade(config, "0078_ai_telemetry_policy")
            tables = set(inspect(schema_engine).get_table_names(schema=schema_name))
            assert "alert_occurrence_metric_cohort_captured_labels" not in tables
            assert "integration_delivery_metric_cohort_taint_labels" not in tables
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_alert_capture_recovery_is_bounded_and_cached(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "migration_0079_metric_captured_taint",
        _BACKEND_DIR / "alembic/versions/0079_metric_captured_taint.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    original = migration._metric_key
    calls = 0

    def counted_metric_key(policy_revision, label_ids):
        nonlocal calls
        calls += 1
        return original(policy_revision, label_ids)

    monkeypatch.setattr(migration, "_metric_key", counted_metric_key)
    cache = {}
    labels = {uuid.uuid4() for _ in range(20)}
    result = migration._matching_alert_capture(
        policy_cohort_key="f" * 64,
        label_ids=labels,
        maximum_revision=1_000_000,
        cache=cache,
    )
    first_calls = calls
    assert result is None
    assert first_calls <= migration._CAPTURE_SEARCH_BUDGET
    assert (
        migration._matching_alert_capture(
            policy_cohort_key="f" * 64,
            label_ids=labels,
            maximum_revision=1_000_000,
            cache=cache,
        )
        is None
    )
    assert calls == first_calls


def _insert_labels(connection, label_ids: list[uuid.UUID]) -> None:
    for index, label_id in enumerate(label_ids):
        connection.execute(
            text(
                "INSERT INTO handling_labels (id, key, name, color) VALUES "
                "(:id, :key, :name, '#B91C1C')"
            ),
            {
                "id": label_id,
                "key": f"migration-0079-{index}-{label_id.hex[:8]}",
                "name": f"Migration 0079 label {index}",
            },
        )


def _insert_alert_metric(
    connection,
    *,
    metric_id: uuid.UUID,
    owner_id: uuid.UUID,
    bucket: str,
    severity: str,
    count: int,
) -> None:
    connection.execute(
        text(
            "INSERT INTO alert_occurrence_metrics "
            "(id, bucket_start, owner_user_id, severity, lifecycle_state, "
            "suppressed, occurrence_count) VALUES "
            "(:id, :bucket, :owner_id, :severity, 'closed', false, :count)"
        ),
        {
            "id": metric_id,
            "bucket": bucket,
            "owner_id": owner_id,
            "severity": severity,
            "count": count,
        },
    )


def _insert_effective_labels(
    connection,
    *,
    table: str,
    cohort_id: uuid.UUID,
    label_ids: set[uuid.UUID],
) -> None:
    for label_id in label_ids:
        connection.execute(
            text(
                f"INSERT INTO {table} (cohort_id, label_id) "
                "VALUES (:cohort_id, :label_id)"
            ),
            {"cohort_id": cohort_id, "label_id": label_id},
        )


def _labels(connection, table: str, cohort_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        connection.scalars(
            text(f"SELECT label_id FROM {table} WHERE cohort_id = :cohort_id"),
            {"cohort_id": cohort_id},
        ).all()
    )
