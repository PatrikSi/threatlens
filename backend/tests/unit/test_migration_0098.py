import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from app.models.ai_provider_budget import AIProviderBudgetReservation
from app.services.ai_config import get_or_create_ai_settings


@pytest.fixture
def migration(db_session, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0098_ai_provider_operations.py"
    spec = importlib.util.spec_from_file_location("ai_operations_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db_session.connection())))
    return module


def test_downgrade_protects_configured_budgets_and_active_leases(db_session, migration):
    settings = get_or_create_ai_settings(db_session)
    settings.hourly_token_budget = 10000
    db_session.flush()
    with pytest.raises(RuntimeError, match="Disable provider workload budgets"):
        migration.downgrade()
    settings.hourly_token_budget = 0
    reservation = AIProviderBudgetReservation(provider_key="legacy", reserved_tokens=100,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    db_session.add(reservation)
    db_session.flush()
    with pytest.raises(RuntimeError, match="active provider admission leases"):
        migration.downgrade()
    reservation.completed_at = datetime.now(timezone.utc)
    db_session.flush()
    settings_id = settings.id
    migration.downgrade()
    assert db_session.scalar(text("SELECT model FROM ai_settings WHERE id=:id"), {"id": settings_id}) == settings.model
    migration.upgrade()
    row = db_session.execute(text("SELECT max_concurrent_requests, hourly_token_budget FROM ai_settings WHERE id=:id"), {"id": settings_id}).one()
    assert tuple(row) == (0, 0)


def test_new_ai_models_match_migrated_tables(db_session):
    names = {"ai_provider_budget_states", "ai_provider_budget_reservations", "ai_workflow_dispatches",
             "ai_reprocess_members", "ai_report_stage_artifacts", "ai_settings", "ai_provider_configurations", "ai_usage_events"}

    def include_object(obj, name, type_, reflected, compare_to):
        return name in names if type_ == "table" else getattr(getattr(obj, "table", None), "name", None) in names

    context = MigrationContext.configure(db_session.connection(), opts={"include_object": include_object})
    assert compare_metadata(context, Base.metadata) == []
