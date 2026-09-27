"""Digest identity upgrade preserves legacy identity and refuses lossy rollback."""

import hashlib

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

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
