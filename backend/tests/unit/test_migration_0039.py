from __future__ import annotations

import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.feed import Feed
from app.models.item import Item
from app.services.connectors.base import NormalizedItem
from app.services.dedupe import content_hash
from app.services.feed_pipeline import upsert_item_from_parsed
from app.services.url_utils import extract_url_domain, normalize_url


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_migration_module():
    migration_path = _BACKEND_DIR / "alembic" / "versions" / "0039_repair_guid_dedupe_keys.py"
    spec = spec_from_file_location("migration_0039_guid_dedupe_repair", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema_name}"})
    return url.render_as_string(hide_password=False)


def test_already_0038_stale_guid_key_upgrades_and_runtime_upsert_finds_item(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    schema_name = f"migration_0039_{uuid.uuid4().hex}"
    schema_database_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_database_url)

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_database_url.replace("%", "%%"))
            get_settings.cache_clear()
            config = _alembic_config()
            command.upgrade(config, "0038_integration_credentials")

            feed_id = uuid.UUID("10000000-0000-0000-0000-000000000039")
            deleted_feed_id = uuid.UUID("20000000-0000-0000-0000-000000000022")
            item_id = uuid.UUID("30000000-0000-0000-0000-000000000022")
            source_guid = "stable-source-guid"
            item_url = normalize_url("https://example.com/advisories/stable") or ""
            title = "Stable advisory"
            summary = "Already stored before the repair migration."
            expected_key = f"guid:{feed_id}:{source_guid}"
            stale_key = f"guid:{deleted_feed_id}:{source_guid}"

            with schema_engine.begin() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
                assert revision == "0038_integration_credentials"
                connection.execute(
                    text(
                        """
                        INSERT INTO feeds (id, name, url, url_digest)
                        VALUES (:id, :name, :url, :url_digest)
                        """
                    ),
                    {
                        "id": feed_id,
                        "name": "Canonical feed",
                        "url": "https://example.com/feed.xml",
                        "url_digest": hashlib.sha256(b"canonical-feed").hexdigest(),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO items (
                            id, feed_id, source_guid, url, url_domain, title, summary,
                            dedupe_key, content_hash, status
                        )
                        VALUES (
                            :id, :feed_id, :source_guid, :url, :url_domain, :title, :summary,
                            :dedupe_key, :content_hash, 'new'
                        )
                        """
                    ),
                    {
                        "id": item_id,
                        "feed_id": feed_id,
                        "source_guid": source_guid,
                        "url": item_url,
                        "url_domain": extract_url_domain(item_url),
                        "title": title,
                        "summary": summary,
                        "dedupe_key": stale_key,
                        "content_hash": content_hash(title, summary, item_url),
                    },
                )

            command.upgrade(config, "head")

            with Session(schema_engine) as session:
                feed = session.get(Feed, feed_id)
                assert feed is not None
                stored_item = session.get(Item, item_id)
                assert stored_item is not None
                assert stored_item.dedupe_key == expected_key

                resolved_item, changed, is_new = upsert_item_from_parsed(
                    session,
                    feed,
                    NormalizedItem(
                        guid=source_guid,
                        url=item_url,
                        title=title,
                        summary=summary,
                        published_at=None,
                    ),
                )

                assert resolved_item.id == item_id
                assert changed is False
                assert is_new is False
                assert session.scalar(select(func.count(Item.id))) == 1

            command.downgrade(config, "0038_integration_credentials")
            with schema_engine.connect() as connection:
                repaired_key = connection.scalar(
                    text("SELECT dedupe_key FROM items WHERE id = :id"),
                    {"id": item_id},
                )
                assert repaired_key == expected_key
    finally:
        schema_engine.dispose()
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_guid_key_collision_preflight_is_deterministic_and_preserves_rows(db_session: Session):
    migration = _load_migration_module()
    feed_id = uuid.UUID("40000000-0000-0000-0000-000000000039")
    first_item_id = uuid.UUID("50000000-0000-0000-0000-000000000001")
    second_item_id = uuid.UUID("50000000-0000-0000-0000-000000000002")
    first_stale_key = "guid:60000000-0000-0000-0000-000000000001:collision-guid"
    second_stale_key = "guid:60000000-0000-0000-0000-000000000002:collision-guid"

    db_session.execute(
        text(
            """
            INSERT INTO feeds (id, name, url, url_digest)
            VALUES (:id, 'Collision feed', 'https://example.com/collision.xml', :url_digest)
            """
        ),
        {"id": feed_id, "url_digest": hashlib.sha256(b"collision-feed").hexdigest()},
    )
    db_session.execute(
        text(
            """
            INSERT INTO items (id, feed_id, source_guid, url, title, dedupe_key, content_hash, status)
            VALUES
                (:first_id, :feed_id, 'collision-guid', 'https://example.com/1', 'One', :first_key, :first_hash, 'new'),
                (
                    :second_id, :feed_id, ' collision-guid ', 'https://example.com/2',
                    'Two', :second_key, :second_hash, 'new'
                )
            """
        ),
        {
            "first_id": first_item_id,
            "second_id": second_item_id,
            "feed_id": feed_id,
            "first_key": first_stale_key,
            "second_key": second_stale_key,
            "first_hash": "1" * 64,
            "second_hash": "2" * 64,
        },
    )

    expected_target = f"guid:{feed_id}:collision-guid"
    expected_message = (
        f"Cannot repair GUID dedupe keys because collisions exist: target key {expected_target!r} "
        f"item_ids={first_item_id},{second_item_id}"
    )
    with pytest.raises(RuntimeError, match="collisions exist") as exc_info:
        migration._repair_guid_dedupe_keys(db_session.connection())

    assert str(exc_info.value) == expected_message
    rows = db_session.execute(
        text("SELECT id, dedupe_key FROM items WHERE id IN (:first_id, :second_id) ORDER BY id"),
        {"first_id": first_item_id, "second_id": second_item_id},
    ).all()
    assert rows == [(first_item_id, first_stale_key), (second_item_id, second_stale_key)]
