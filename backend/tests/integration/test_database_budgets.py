import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.core.api_errors import install_api_error_handlers
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.db.session import _engine_options


def test_runtime_pool_has_explicit_finite_limits():
    options = _engine_options("postgresql+psycopg://local@localhost/example")
    assert options["pool_size"] == 2
    assert options["max_overflow"] == 0
    assert "lock_timeout=" in options["connect_args"]["options"]
    assert "idle_in_transaction_session_timeout" not in options["connect_args"]["options"]


def test_multiple_statements_share_one_operation_deadline(db_session):
    started = time.monotonic()
    with pytest.raises(OperationalError) as failure:
        with database_operation(db_session, operation="repair", timeout_seconds=0.25):
            db_session.execute(text("SELECT pg_sleep(0.15)"))
            db_session.execute(text("SELECT pg_sleep(0.20)"))
    assert failure.value.orig.sqlstate == "57014"
    assert time.monotonic() - started < 1.5
    assert db_session.scalar(text("SELECT 1")) == 1


def test_deadline_rejects_late_commit_and_rolls_back_changes(db_session):
    db_session.execute(text("CREATE TEMP TABLE budget_probe(value integer)"))
    db_session.commit()
    with pytest.raises(DatabaseDeadlineExceeded):
        with database_operation(db_session, operation="repair", timeout_seconds=0.05):
            db_session.execute(text("INSERT INTO budget_probe VALUES (1)"))
            time.sleep(0.07)
            db_session.commit()
    assert db_session.scalar(text("SELECT count(*) FROM budget_probe")) == 0


def test_scope_restores_statement_settings(db_session):
    before = db_session.execute(text("SHOW statement_timeout")).scalar_one()
    with database_operation(db_session, operation="lifecycle", timeout_seconds=0.5):
        db_session.execute(text("SELECT 1"))
        during = db_session.execute(text("SHOW statement_timeout")).scalar_one()
        assert during != before
    assert db_session.execute(text("SHOW statement_timeout")).scalar_one() == before


def test_lock_wait_and_pool_saturation_are_bounded(database_engine):
    engine = database_engine
    limited = create_engine(
        engine.url, pool_size=1, max_overflow=0, pool_timeout=0.05,
        connect_args={"options": "-c lock_timeout=60"},
    )
    try:
        with Session(engine) as owner, Session(limited) as waiter:
            owner.execute(text("SELECT pg_advisory_xact_lock(1786423145)"))
            started = time.monotonic()
            with pytest.raises(OperationalError) as failure:
                waiter.execute(text("SELECT pg_advisory_xact_lock(1786423145)"))
            assert failure.value.orig.sqlstate == "55P03"
            assert time.monotonic() - started < 1
            waiter.rollback()
            owner.rollback()
            assert waiter.scalar(text("SELECT 1")) == 1
            with pytest.raises(PoolTimeoutError):
                with limited.connect():
                    pass
    finally:
        limited.dispose()


def test_database_saturation_returns_retryable_sanitized_response():
    app = FastAPI()
    install_api_error_handlers(app)

    @app.get("/busy")
    def busy():
        raise PoolTimeoutError("sensitive internal connection details")

    response = TestClient(app).get("/busy")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.json()["error"]["retryable"] is True
    assert "sensitive" not in response.text


def test_deferred_commit_work_uses_remaining_transaction_deadline(database_engine):
    with Session(database_engine) as db:
        db.execute(text("CREATE TEMP TABLE commit_budget_probe(value integer)"))
        db.execute(text("""
            CREATE FUNCTION pg_temp.delay_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$
        """))
        db.execute(text("""
            CREATE CONSTRAINT TRIGGER slow_commit AFTER INSERT ON commit_budget_probe
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.delay_commit()
        """))
        db.commit()
        started = time.monotonic()
        with pytest.raises(OperationalError) as failure:
            with database_operation(db, operation="repair", timeout_seconds=0.25):
                db.execute(text("INSERT INTO commit_budget_probe VALUES (1)"))
                db.execute(text("SELECT pg_sleep(0.15)"))
                db.commit()
        assert failure.value.orig.sqlstate == "57014"
        assert time.monotonic() - started < 1.5
        assert db.scalar(text("SELECT count(*) FROM commit_budget_probe")) == 0
