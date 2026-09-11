import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, text

from app.models.ai_provider import AIProviderConfiguration
from app.models.ai_task_run import AITaskRun
from app.services.ai_config import get_or_create_ai_settings


@pytest.fixture()
def provider_migration(db_session, monkeypatch):
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
    return migration


def test_provider_migration_preserves_legacy_and_requires_explicit_provider_removal(
    db_session, provider_migration
):
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
        provider_migration.downgrade()
    assert (
        db_session.scalar(text("SELECT count(*) FROM ai_provider_configurations")) == 1
    )
    db_session.execute(delete(AIProviderConfiguration))
    provider_migration.downgrade()
    assert (
        db_session.scalar(text("SELECT model FROM ai_settings WHERE singleton_key=1"))
        == "legacy-preserved"
    )
    provider_migration.upgrade()
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


@pytest.mark.parametrize(
    "task_type", ["item_enrichment", "daily_brief", "report", "reprocess"]
)
@pytest.mark.parametrize("status", ["queued", "running"])
def test_provider_downgrade_blocks_outstanding_work_after_profile_deletion(
    db_session, provider_migration, task_type, status
):
    provider_id = uuid.uuid4()
    db_session.add(
        AIProviderConfiguration(
            id=provider_id,
            name="Removed",
            normalized_name="removed",
            base_url="https://example.com",
            model="new",
        )
    )
    run = AITaskRun(
        task_type=task_type,
        trigger_source="manual",
        status=status,
        metadata_json={
            "provider_selection": {"provider_id": str(provider_id), "version": 1}
        },
    )
    db_session.add(run)
    db_session.flush()
    db_session.execute(
        delete(AIProviderConfiguration).where(AIProviderConfiguration.id == provider_id)
    )

    with pytest.raises(RuntimeError, match="outstanding named-provider AI tasks"):
        provider_migration.downgrade()

    assert db_session.scalar(text("SELECT count(*) FROM ai_provider_routing")) == 1
    assert (
        db_session.scalar(text("SELECT count(*) FROM ai_provider_configurations")) == 0
    )
    assert run.status == status and run.finished_at is None


@pytest.mark.parametrize("status", ["ready", "error", "skipped", "queued", "running"])
def test_provider_downgrade_preserves_finished_named_history_and_legacy_work(
    db_session, provider_migration, status
):
    run = AITaskRun(
        task_type="reprocess",
        trigger_source="manual",
        status=status,
        finished_at=datetime.now(timezone.utc),
        metadata_json={
            "provider_selection": {"provider_id": str(uuid.uuid4()), "version": 1}
        },
    )
    legacy = AITaskRun(
        task_type="reprocess",
        trigger_source="manual",
        status="queued",
        metadata_json={},
    )
    explicit_legacy = AITaskRun(
        task_type="reprocess",
        trigger_source="manual",
        status="queued",
        metadata_json={"provider_selection": {"provider_id": None, "version": None}},
    )
    db_session.add_all([run, legacy, explicit_legacy])
    db_session.flush()

    provider_migration.downgrade()

    assert db_session.scalar(text("SELECT count(*) FROM ai_task_runs")) == 3
    assert (
        db_session.scalar(
            text(
                "SELECT metadata_json #>> '{provider_selection,provider_id}' FROM ai_task_runs WHERE id=:id"
            ),
            {"id": run.id},
        )
        == run.metadata_json["provider_selection"]["provider_id"]
    )
    provider_migration.upgrade()
