"""New envelopes avoid known-empty reads without weakening lineage checks."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
import uuid

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyState,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.services import data_access_envelopes as envelopes, data_access_lineage as lineage


def _source(revision, *, label=UNRESTRICTED_HANDLING_LABEL_ID):
    return envelopes.DataAccessSourceInput(
        source_type="system",
        source_id=str(uuid.uuid4()),
        source_version="query-control-v1",
        handling_label_id=label,
        captured_policy_revision=revision,
    )


@contextmanager
def _select_counts(engine):
    counts = Counter()

    def count(_connection, _cursor, _statement, _parameters, context, _many):
        compiled = getattr(context, "compiled", None)
        statement = getattr(compiled, "statement", None)
        if statement is None or not getattr(statement, "is_select", False):
            return
        tables = frozenset(
            node.name for node in visitors.iterate(statement) if isinstance(node, Table)
        )
        if len(tables) != 1:
            return
        lock = getattr(statement, "_for_update_arg", None)
        kind = "share" if lock is not None and lock.read else (
            "update" if lock is not None else "none"
        )
        counts[(next(iter(tables)), kind)] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", count)


@pytest.mark.parametrize("operation", ["new", "replay"])
def test_normalized_envelope_reads_once_per_required_persisted_snapshot(db_session, operation):
    assert db_session.bind.dialect.name == "postgresql"
    resource_id = uuid.uuid4()
    revision = db_session.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    source = _source(revision)
    if operation == "replay":
        envelopes.put_data_access_envelope_sources(
            db_session, resource_type="report", resource_id=resource_id, sources=[source]
        )
    engine = db_session.get_bind()
    with _select_counts(engine) as counts:
        snapshot = envelopes.merge_data_access_envelope_sources(
            db_session, resource_type="report", resource_id=resource_id, sources=[source]
        )
    assert snapshot.source_count == 1
    assert snapshot.label_counts == {UNRESTRICTED_HANDLING_LABEL_ID: 1}
    assert counts[("data_access_envelopes", "update")] == 1
    assert counts[("data_access_envelope_sources", "update")] == 1
    assert counts[("data_access_envelope_labels", "none")] == 1
    # Both fresh and replay paths retain the policy fence and all active-label
    # checks, including the persisted post-flush validation for new lineage.
    assert counts[("data_policy_state", "share")] == 1
    assert counts[("handling_labels", "none")] == (3 if operation == "new" else 2)


def test_invalid_source_is_rejected_before_envelope_creation(db_session):
    revision = db_session.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    source = replace(_source(revision), source_feed_id=uuid.uuid4())
    resource_id = uuid.uuid4()
    with pytest.raises(envelopes.DataAccessEnvelopeConflict, match="do not exist"):
        envelopes.put_data_access_envelope_sources(
            db_session, resource_type="report", resource_id=resource_id, sources=[source]
        )
    assert db_session.scalar(
        select(func.count()).select_from(DataAccessEnvelope).where(DataAccessEnvelope.resource_id == resource_id)
    ) == 0


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_envelope_winner_is_reloaded_and_lineage_is_preserved(database_engine, monkeypatch, conflicting):
    """The missed lookup races with a committed winner on a second connection."""
    assert database_engine.dialect.name == "postgresql"
    resource_id = uuid.uuid4()
    with Session(database_engine, autoflush=False) as db:
        revision = db.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    source = _source(revision)
    winner_label = QUARANTINE_HANDLING_LABEL_ID if conflicting else UNRESTRICTED_HANDLING_LABEL_ID
    winner_source = replace(source, handling_label_id=winner_label)
    missed = Event()
    winner_committed = Event()
    creator = [None]
    unique_errors = []
    reload_reads = []
    lookup = envelopes._get_envelope_model

    def paused_lookup(db, **kwargs):
        result = lookup(db, **kwargs)
        if db is creator[0] and kwargs["resource_id"] == resource_id:
            if not missed.is_set():
                assert result is None
                missed.set()
                assert winner_committed.wait(timeout=5)
            else:
                reload_reads.append(result is not None)
        return result

    def query_failed(context):
        if isinstance(context.sqlalchemy_exception, IntegrityError):
            unique_errors.append(True)

    monkeypatch.setattr(envelopes, "_get_envelope_model", paused_lookup)
    event.listen(database_engine, "handle_error", query_failed)

    def create_competing():
        with Session(database_engine, autoflush=False) as db:
            creator[0] = db
            if conflicting:
                with pytest.raises(envelopes.DataAccessEnvelopeConflict, match="different provenance"):
                    envelopes.put_data_access_envelope_sources(
                        db, resource_type="report", resource_id=resource_id, sources=[source]
                    )
            else:
                reused = envelopes.put_data_access_envelope_sources(
                    db, resource_type="report", resource_id=resource_id, sources=[source]
                )
                assert reused.label_counts == {winner_label: 1}
            # The source conflict rolls back only the service savepoint; the
            # Session remains usable and the winning envelope stays unchanged.
            assert db.scalar(select(1)) == 1
            db.rollback()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(create_competing)
            try:
                assert missed.wait(timeout=5)
                with Session(database_engine, autoflush=False) as db:
                    winner = envelopes.put_data_access_envelope_sources(
                        db, resource_type="report", resource_id=resource_id, sources=[winner_source]
                    )
                    db.commit()
            finally:
                winner_committed.set()
            future.result(timeout=10)
        assert unique_errors == [True]
        assert reload_reads == [True]
        with Session(database_engine, autoflush=False) as db:
            snapshot = envelopes.get_data_access_envelope(db, resource_type="report", resource_id=resource_id)
            assert snapshot == winner
            assert snapshot.label_counts == {winner_label: 1}
            assert len(envelopes.get_data_access_envelope_sources(db, resource_type="report", resource_id=resource_id)) == 1
    finally:
        event.remove(database_engine, "handle_error", query_failed)
        with Session(database_engine, autoflush=False) as db:
            db.execute(delete(DataAccessEnvelope).where(DataAccessEnvelope.resource_id == resource_id))
            db.commit()
            assert db.scalar(select(func.count()).select_from(DataAccessEnvelope).where(DataAccessEnvelope.resource_id == resource_id)) == 0


def test_missing_unique_conflict_reload_fails_closed(database_engine, monkeypatch):
    resource_id = uuid.uuid4()
    with Session(database_engine, autoflush=False) as db:
        revision = db.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
        source = _source(revision)
        envelopes.put_data_access_envelope_sources(db, resource_type="report", resource_id=resource_id, sources=[source])
        db.commit()
    monkeypatch.setattr(envelopes, "_get_envelope_model", lambda *_args, **_kwargs: None)
    try:
        with Session(database_engine, autoflush=False) as db:
            with pytest.raises(envelopes.DataPolicyUnavailable, match="created or reloaded"):
                envelopes.put_data_access_envelope_sources(db, resource_type="report", resource_id=resource_id, sources=[source])
            assert db.scalar(select(1)) == 1
            db.rollback()
    finally:
        with Session(database_engine, autoflush=False) as db:
            db.execute(delete(DataAccessEnvelope).where(DataAccessEnvelope.resource_id == resource_id))
            db.commit()


def test_post_flush_persisted_invariant_failure_rolls_back_new_envelope(db_session):
    resource_id = uuid.uuid4()
    revision = db_session.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    source = _source(revision)
    connection = db_session.get_bind()
    tampered = []
    envelope_ids = []

    def corrupt_persisted_label(_connection, _cursor, _statement, _parameters, context, _many):
        statement = getattr(getattr(context, "compiled", None), "statement", None)
        if (not tampered and statement is not None and getattr(statement, "is_insert", False)
                and statement.table.name == "data_access_envelope_labels"):
            tampered.append(True)
            envelope_ids.extend(_connection.scalars(
                select(DataAccessEnvelope.id).where(DataAccessEnvelope.resource_id == resource_id)
            ))
            _connection.execute(
                update(DataAccessEnvelopeLabel).where(DataAccessEnvelopeLabel.envelope_id.in_(
                    select(DataAccessEnvelope.id).where(DataAccessEnvelope.resource_id == resource_id)
                )).values(source_count=2)
            )

    event.listen(connection, "after_cursor_execute", corrupt_persisted_label)
    try:
        with pytest.raises(envelopes.DataPolicyUnavailable, match="inconsistent"):
            envelopes.put_data_access_envelope_sources(
                db_session, resource_type="report", resource_id=resource_id, sources=[source]
            )
    finally:
        event.remove(connection, "after_cursor_execute", corrupt_persisted_label)
    assert tampered == [True]
    assert db_session.scalar(select(1)) == 1
    assert db_session.scalar(select(func.count()).select_from(DataAccessEnvelope).where(
        DataAccessEnvelope.resource_id == resource_id)) == 0
    # The service's enclosing savepoint also removes both child tables.
    assert db_session.scalar(select(func.count()).select_from(DataAccessEnvelopeSource).where(
        DataAccessEnvelopeSource.envelope_id.in_(envelope_ids))) == 0
    assert db_session.scalar(select(func.count()).select_from(DataAccessEnvelopeLabel).where(
        DataAccessEnvelopeLabel.envelope_id.in_(envelope_ids))) == 0


def test_concurrent_winner_cannot_be_its_own_lineage_parent(database_engine, monkeypatch):
    """A parent valid during the miss becomes the reloaded target's own source."""
    resource_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    with Session(database_engine, autoflush=False) as db:
        revision = db.scalar(select(DataPolicyState.revision).where(DataPolicyState.id == 1))
    source = _source(revision)
    nested_source = replace(source, source_parent_id=parent_id)
    missed = Event()
    winner_committed = Event()
    creator = [None]
    unique_errors = []
    lookup = envelopes._get_envelope_model
    source_model = lineage.source_model

    def assign_winner_parent_id(envelope_id, value):
        row = source_model(envelope_id, value)
        if value.source_id == source.source_id and value.source_parent_id is None:
            row.id = parent_id
        return row

    def pause_after_miss(db, **kwargs):
        result = lookup(db, **kwargs)
        if db is creator[0] and kwargs["resource_id"] == resource_id and not missed.is_set():
            assert result is None
            missed.set()
            assert winner_committed.wait(timeout=5)
        return result

    def failed_insert(context):
        if isinstance(context.sqlalchemy_exception, IntegrityError):
            unique_errors.append(True)

    monkeypatch.setattr(lineage, "source_model", assign_winner_parent_id)
    monkeypatch.setattr(envelopes, "_get_envelope_model", pause_after_miss)
    event.listen(database_engine, "handle_error", failed_insert)

    def create_nested():
        with Session(database_engine, autoflush=False) as db:
            creator[0] = db
            with pytest.raises(envelopes.DataAccessEnvelopeConflict, match="own envelope"):
                envelopes.merge_data_access_envelope_sources(
                    db, resource_type="report", resource_id=resource_id, sources=[nested_source]
                )
            assert db.scalar(select(1)) == 1
            db.rollback()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(create_nested)
            try:
                assert missed.wait(timeout=5)
                with Session(database_engine, autoflush=False) as db:
                    winner = envelopes.put_data_access_envelope_sources(
                        db, resource_type="report", resource_id=resource_id, sources=[source]
                    )
                    db.commit()
            finally:
                winner_committed.set()
            future.result(timeout=10)
        assert unique_errors == [True]
        with Session(database_engine, autoflush=False) as db:
            assert envelopes.get_data_access_envelope(db, resource_type="report", resource_id=resource_id) == winner
            stored = envelopes.get_data_access_envelope_sources(db, resource_type="report", resource_id=resource_id)
            assert len(stored) == 1 and stored[0].id == parent_id and stored[0].source_parent_id is None
    finally:
        event.remove(database_engine, "handle_error", failed_insert)
        with Session(database_engine, autoflush=False) as db:
            db.execute(delete(DataAccessEnvelope).where(DataAccessEnvelope.resource_id == resource_id))
            db.commit()
