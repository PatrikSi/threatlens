import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, text

from app.models.ai_provider import AIProviderConfiguration
from app.services.ai_config import get_or_create_ai_settings


def test_provider_migration_preserves_legacy_and_requires_explicit_provider_removal(
    db_session, monkeypatch
):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0095_ai_provider_configurations.py"
    )
    spec = importlib.util.spec_from_file_location("provider_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration, "op", Operations(MigrationContext.configure(db_session.connection()))
    )
    legacy = get_or_create_ai_settings(db_session)
    legacy.model = "legacy-preserved"
    db_session.add(
        AIProviderConfiguration(
            id=uuid.uuid4(),
            name="Named",
            normalized_name="named",
            base_url="https://example.com",
            model="new",
        )
    )
    db_session.flush()
    with pytest.raises(RuntimeError, match="delete named providers"):
        migration.downgrade()
    assert (
        db_session.scalar(text("SELECT count(*) FROM ai_provider_configurations")) == 1
    )
    db_session.execute(delete(AIProviderConfiguration))
    migration.downgrade()
    assert (
        db_session.scalar(text("SELECT model FROM ai_settings WHERE singleton_key=1"))
        == "legacy-preserved"
    )
    migration.upgrade()
    assert (
        db_session.scalar(
            text(
                "SELECT default_provider_id FROM ai_provider_routing WHERE singleton_key=1"
            )
        )
        is None
    )
    assert (
        db_session.scalar(text("SELECT model FROM ai_settings WHERE singleton_key=1"))
        == "legacy-preserved"
    )
