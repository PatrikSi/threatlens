"""Legacy IOC identity survives digest-key upgrades; limited roles can insert."""

import hashlib
import importlib.util
from pathlib import Path
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from app.models.ioc import IOC


def _migration(db, monkeypatch):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0107_intel_assessments.py"
    )
    spec = importlib.util.spec_from_file_location("intel_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "op", Operations(MigrationContext.configure(db.connection()))
    )
    return module


def test_migration_round_trip_preserves_existing_indicator_identity(
    db_session, monkeypatch
):
    row = IOC(type="domain", value_raw="Legacy.net", value_norm="legacy.net")
    db_session.add(row)
    db_session.flush()
    identity = row.id
    migration = _migration(db_session, monkeypatch)
    migration.downgrade()
    assert (
        db_session.scalar(
            text("SELECT value_norm FROM iocs WHERE id=:id"), {"id": identity}
        )
        == "legacy.net"
    )
    migration.upgrade()
    value = db_session.execute(
        text("SELECT id,value_digest FROM iocs WHERE id=:id"), {"id": identity}
    ).one()
    assert (
        value.id == identity
        and value.value_digest == hashlib.sha256(b"legacy.net").hexdigest()
    )
    names = {
        "iocs",
        "item_iocs",
        "item_intel_states",
        "team_intel_states",
        "indicator_assessments",
        "indicator_assessment_history",
        "indicator_suppressions",
        "indicator_suppression_history",
    }

    def include_object(obj, name, kind, reflected, compare_to):
        return (
            name in names
            if kind == "table"
            else getattr(getattr(obj, "table", None), "name", None) in names
        )

    context = MigrationContext.configure(
        db_session.connection(), opts={"include_object": include_object}
    )
    assert compare_metadata(context, Base.metadata) == []


def test_downgrade_refuses_long_values_before_removing_new_schema(
    db_session, monkeypatch
):
    value = "https://evil.net/" + "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(60)
    )
    db_session.add(IOC(type="url", value_raw=value, value_norm=value))
    db_session.flush()
    with pytest.raises(RuntimeError, match="long indicator values remain"):
        _migration(db_session, monkeypatch).downgrade()
    assert (
        db_session.scalar(text("SELECT to_regclass('item_intel_states')")) is not None
    )


def test_generated_digest_is_available_to_limited_runtime_role(db_session):
    role = f"ioc_runtime_{uuid.uuid4().hex[:16]}"
    # Identifier is generated locally and contains only letters/hex/underscore.
    db_session.execute(
        text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    )
    db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    db_session.execute(text(f'GRANT INSERT, SELECT ON iocs TO "{role}"'))
    db_session.execute(text(f'SET LOCAL ROLE "{role}"'))
    try:
        digest = db_session.scalar(
            text(
                "INSERT INTO iocs (id,type,value_raw,value_norm) VALUES (:id,'domain','limited.net','limited.net') RETURNING value_digest"
            ),
            {"id": uuid.uuid4()},
        )
        assert digest == hashlib.sha256(b"limited.net").hexdigest()
    finally:
        db_session.execute(text("RESET ROLE"))
        db_session.execute(text(f'DROP OWNED BY "{role}"'))
        db_session.execute(text(f'DROP ROLE "{role}"'))
