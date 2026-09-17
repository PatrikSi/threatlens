from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from contextlib import contextmanager
import pytest

from app.db.budgets import DatabaseDeadlineExceeded
from app.services import export_download_budget
from app.services.export_download_budget import ExportDownloadPreparationBudget


def test_preparation_keeps_one_deadline_across_transactions(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(export_download_budget, "monotonic", lambda: clock[0])
    budget = ExportDownloadPreparationBudget(deadline=110.0)
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as db:
            with budget.transaction(db):
                db.execute(text("SELECT 1"))
                clock[0] = 109.0
                db.commit()
            with pytest.raises(DatabaseDeadlineExceeded):
                with budget.transaction(db):
                    db.execute(text("SELECT 1"))
                    clock[0] = 111.0
                    budget.checkpoint()
            assert not db.in_transaction()
    finally:
        engine.dispose()


def test_successful_preparation_preserves_the_transfer_transaction():
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as db:
            with ExportDownloadPreparationBudget.start().transaction(db):
                db.execute(text("SELECT 1"))
            assert db.in_transaction()
            assert db.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_preparation_deadline_includes_restoring_transaction_limits(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(export_download_budget, "monotonic", lambda: clock[0])

    @contextmanager
    def transaction_with_slow_teardown(*args, **kwargs):
        yield
        clock[0] = 111.0

    monkeypatch.setattr(export_download_budget, "database_operation", transaction_with_slow_teardown)
    with pytest.raises(DatabaseDeadlineExceeded):
        with ExportDownloadPreparationBudget(deadline=110.0).transaction(object()):
            clock[0] = 109.0
