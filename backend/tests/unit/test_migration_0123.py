"""Digest identity upgrade preserves legacy identity and refuses lossy rollback."""

import hashlib
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db.base import Base
from tests.unit.test_migration_0107 import _migration
from tests.unit.test_migration_0115 import source


def test_identity_upgrade_preserves_legacy_rows(db_session, monkeypatch):
    item = source(db_session)
    item.source_guid = "Legacy identity"
    db_session.flush()
    identifier, key = item.id, item.dedupe_key
    migration = _migration(db_session, monkeypatch, "0123_item_identity_indexes")
    migration.downgrade()
    assert db_session.scalar(text("SELECT source_guid FROM items WHERE id=:id"), {"id": identifier}) == "Legacy identity"
    migration.upgrade()
    db_session.expire(item)
    assert item.id == identifier and item.dedupe_key == key
    assert item.dedupe_digest == hashlib.sha256(key.encode()).hexdigest()
    context = MigrationContext.configure(db_session.connection(), opts={"include_object":
        lambda obj, name, kind, reflected, compared: name == "items" if kind == "table"
        else getattr(getattr(obj, "table", None), "name", None) == "items"})
    assert compare_metadata(context, Base.metadata) == []


@pytest.mark.parametrize("column", ["source_guid", "dedupe_key", "canonical_url"])
def test_rollback_preserves_long_evidence(db_session, monkeypatch, column):
    item = source(db_session)
    setattr(item, column, "資料" * 1500)
    db_session.flush()
    migration = _migration(db_session, monkeypatch, "0123_item_identity_indexes")
    with pytest.raises(RuntimeError, match="Retained long item identities"):
        migration.downgrade()
    db_session.expire(item)
    assert getattr(item, column) == "資料" * 1500


def test_generated_item_digests_work_with_limited_runtime_privileges(db_session):
    existing = source(db_session)
    feed_id = existing.feed_id
    role = f"item_runtime_{uuid.uuid4().hex[:16]}"
    db_session.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'))
    db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    db_session.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON items TO "{role}"'))
    # Match provision-roles.sh: no EXECUTE grant or elevated membership is
    # needed for the migration-created function and its pg_catalog primitives.
    db_session.execute(text(f'SET LOCAL ROLE "{role}"'))
    try:
        assert db_session.scalar(text("SELECT current_user")) == role
        assert not db_session.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        assert db_session.scalar(text("SELECT has_function_privilege(current_user, 'threatlens_item_identity_digest(text)', 'EXECUTE')"))
        identity = "資料" + "".join(hashlib.sha256(str(index).encode()).hexdigest() for index in range(80))
        identifier, dedupe = uuid.uuid4(), "feed:" + identity
        row = db_session.execute(text('''
            INSERT INTO items(id,feed_id,title,url,source_guid,canonical_url,dedupe_key,content_hash)
            VALUES (:id,:feed,'Limited runtime','https://source.example/article',:guid,:url,:dedupe,:hash)
            RETURNING dedupe_digest,source_guid_digest
        '''), {"id": identifier, "feed": feed_id, "guid": identity, "url": "https://source.example/" + identity,
               "dedupe": dedupe, "hash": "a" * 64}).one()
        assert row.dedupe_digest == hashlib.sha256(dedupe.encode("utf-8")).hexdigest()
        assert row.source_guid_digest == hashlib.sha256(identity.encode("utf-8")).hexdigest()
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.execute(text('''
                INSERT INTO items(id,feed_id,title,url,source_guid,dedupe_key,content_hash)
                VALUES (:id,:feed,'Duplicate','https://source.example/duplicate',:guid,:dedupe,:hash)
            '''), {"id": uuid.uuid4(), "feed": feed_id, "guid": identity,
                   "dedupe": str(uuid.uuid4()), "hash": "b" * 64})
        changed = identity + "updated"
        digest = db_session.scalar(text("UPDATE items SET source_guid=:guid WHERE id=:id RETURNING source_guid_digest"),
            {"id": identifier, "guid": changed})
        assert digest == hashlib.sha256(changed.encode("utf-8")).hexdigest()
        assert db_session.scalar(text("UPDATE items SET source_guid=NULL WHERE id=:id RETURNING source_guid_digest"),
            {"id": identifier}) is None
        with pytest.raises(ProgrammingError), db_session.begin_nested():
            db_session.execute(text('CREATE TABLE public.forbidden_runtime_ddl (id integer)'))
    finally:
        db_session.execute(text("RESET ROLE"))
        db_session.execute(text(f'DROP OWNED BY "{role}"'))
        db_session.execute(text(f'DROP ROLE "{role}"'))
