from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.models.data_policy import QUARANTINE_HANDLING_LABEL_ID


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


def test_data_access_lineage_migration_backfills_and_preserves_aggregates(
    test_database_url, monkeypatch
):
    schema_name = f"migration_0071_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)
    restricted_label_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    report_id = uuid.uuid4()
    empty_report_id = uuid.uuid4()
    orphan_report_id = uuid.uuid4()
    report_source_id = uuid.uuid4()
    report_envelope_id = uuid.uuid4()
    empty_envelope_id = uuid.uuid4()
    orphan_envelope_id = uuid.uuid4()

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
            command.upgrade(config, "0070_data_access_envelopes")

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO handling_labels (id, key, name, color) "
                        "VALUES (:id, 'migration-0071', 'Migration 0071', '#B91C1C')"
                    ),
                    {"id": restricted_label_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO feeds "
                        "(id, name, url, url_digest, handling_label_id) VALUES "
                        "(:id, 'Migration feed', 'encrypted', :digest, :label_id)"
                    ),
                    {
                        "id": feed_id,
                        "digest": "f" * 64,
                        "label_id": restricted_label_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO items "
                        "(id, feed_id, url, title, dedupe_key, content_hash, status) "
                        "VALUES (:id, :feed_id, 'https://example.com/item', "
                        "'Migration item', 'migration-0071-item', :digest, 'content_fetched')"
                    ),
                    {"id": item_id, "feed_id": feed_id, "digest": "a" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO reports "
                        "(id, title, period_start, period_end, filters_json, "
                        "prompt_config_json, sections_config_json, metrics_json, "
                        "coverage_json) VALUES "
                        "(:report_id, 'Sourced report', now(), now(), '{}'::json, "
                        "'{}'::json, '[]'::json, '{}'::json, '{}'::json), "
                        "(:empty_id, 'Empty report', now(), now(), '{}'::json, "
                        "'{}'::json, '[]'::json, '{}'::json, '{}'::json)"
                    ),
                    {"report_id": report_id, "empty_id": empty_report_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO report_source_items "
                        "(id, report_id, item_id, citation_key, title_snapshot, "
                        "feed_name_snapshot, url_snapshot, first_seen_at_snapshot, "
                        "tags_snapshot_json, iocs_snapshot_json) VALUES "
                        "(:id, :report_id, :item_id, 'S1', 'Migration item', "
                        "'Migration feed', 'https://example.com/item', now(), "
                        "'[]'::json, '[]'::json)"
                    ),
                    {
                        "id": report_source_id,
                        "report_id": report_id,
                        "item_id": item_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelopes "
                        "(id, resource_type, resource_id, source_count, policy_revision) "
                        "VALUES "
                        "(:report_envelope, 'report', :report_id, 9, 1), "
                        "(:empty_envelope, 'report', :empty_id, 4, 1), "
                        "(:orphan_envelope, 'report', :orphan_id, 2, 1)"
                    ),
                    {
                        "report_envelope": report_envelope_id,
                        "report_id": report_id,
                        "empty_envelope": empty_envelope_id,
                        "empty_id": empty_report_id,
                        "orphan_envelope": orphan_envelope_id,
                        "orphan_id": orphan_report_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO data_access_envelope_labels "
                        "(envelope_id, label_id, source_count) VALUES "
                        "(:report_envelope, :quarantine, 9), "
                        "(:empty_envelope, :quarantine, 4), "
                        "(:orphan_envelope, :quarantine, 2)"
                    ),
                    {
                        "report_envelope": report_envelope_id,
                        "empty_envelope": empty_envelope_id,
                        "orphan_envelope": orphan_envelope_id,
                        "quarantine": QUARANTINE_HANDLING_LABEL_ID,
                    },
                )

            command.upgrade(config, "0071_data_access_lineage")
            inspector = inspect(schema_engine)
            assert "data_access_envelope_sources" in inspector.get_table_names(
                schema=schema_name
            )

            with schema_engine.begin() as connection:
                source = connection.execute(
                    text(
                        "SELECT source_type, source_id, source_version, "
                        "source_feed_id, handling_label_id, source_digest "
                        "FROM data_access_envelope_sources "
                        "WHERE envelope_id = :envelope_id"
                    ),
                    {"envelope_id": report_envelope_id},
                ).one()
                assert source == (
                    "item",
                    str(item_id),
                    str(report_source_id),
                    feed_id,
                    restricted_label_id,
                    "a" * 64,
                )
                aggregate = connection.execute(
                    text(
                        "SELECT envelope.source_count, envelope.policy_revision, "
                        "label.label_id, label.source_count "
                        "FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_labels AS label "
                        "ON label.envelope_id = envelope.id "
                        "WHERE envelope.id = :envelope_id"
                    ),
                    {"envelope_id": report_envelope_id},
                ).one()
                assert aggregate[0] == aggregate[3] == 1
                assert aggregate[1] >= 1
                assert aggregate[2] == restricted_label_id

                unresolved = connection.execute(
                    text(
                        "SELECT source_type, handling_label_id "
                        "FROM data_access_envelope_sources "
                        "WHERE envelope_id = :envelope_id"
                    ),
                    {"envelope_id": empty_envelope_id},
                ).one()
                assert unresolved == ("unresolved", QUARANTINE_HANDLING_LABEL_ID)
                assert connection.execute(
                    text(
                        "SELECT envelope.source_count, label.label_id, "
                        "label.source_count FROM data_access_envelopes AS envelope "
                        "JOIN data_access_envelope_labels AS label "
                        "ON label.envelope_id = envelope.id "
                        "WHERE envelope.id = :envelope_id"
                    ),
                    {"envelope_id": empty_envelope_id},
                ).one() == (1, QUARANTINE_HANDLING_LABEL_ID, 1)
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM data_access_envelopes WHERE id = :id"
                        ),
                        {"id": orphan_envelope_id},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT coverage_version FROM data_policy_state WHERE id = 1"
                        )
                    )
                    == 0
                )

            command.downgrade(config, "0070_data_access_envelopes")
            inspector.clear_cache()
            assert "data_access_envelope_sources" not in inspector.get_table_names(
                schema=schema_name
            )
            with schema_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT label_id FROM data_access_envelope_labels "
                            "WHERE envelope_id = :envelope_id"
                        ),
                        {"envelope_id": report_envelope_id},
                    )
                    == restricted_label_id
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT label_id FROM data_access_envelope_labels "
                            "WHERE envelope_id = :envelope_id"
                        ),
                        {"envelope_id": empty_envelope_id},
                    )
                    == QUARANTINE_HANDLING_LABEL_ID
                )
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
