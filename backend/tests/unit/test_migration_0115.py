"""Populated upgrades and non-destructive rollback for the intelligence expansion."""

from datetime import datetime, timedelta, timezone
import uuid

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.base import Base
from app.models.ai_provider import AIProviderConfiguration, AIProviderRouting
from app.models.ai_provider_budget import AIProviderBudgetReservation
from app.models.automation_execution import AutomationExecution
from app.models.feed import Feed
from app.models.iam import IAMGroup
from app.models.indicator_publication import IndicatorPublication
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.team import Team
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_item_assessment import TeamItemAssessment
from app.models.user import User
from tests.unit.test_migration_0107 import _migration

REVISIONS = (
    "0111_extraction_sections",
    "0112_automation_executions",
    "0113_ai_quota_groups",
    "0114_hunt_worklist",
    "0115_reviewed_publications",
)
LATER_REVISIONS = (
    "0116_team_integrations",
    "0117_article_continuations",
    "0118_ai_qualification",
    "0119_team_ai_governance",
    "0120_hunt_review_workflow",
    "0121_publication_distribution",
    "0122_mcp_delegation",
    "0123_item_identity_indexes",
    "0124_reconciliation_progress",
)


def _round_trip_legacy(db, monkeypatch, revision):
    # Dependent head tables must be removed first, just as Alembic traverses the
    # graph. Rebuilding one historical table in isolation loses later columns.
    later = [_migration(db, monkeypatch, name) for name in LATER_REVISIONS]
    for entry in reversed(later):
        entry.downgrade()
    migration = _migration(db, monkeypatch, revision)
    migration.downgrade()
    migration.upgrade()
    for entry in later:
        entry.upgrade()


def source(db):
    feed = Feed(name="Migration source", url=f"https://source.example/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title="Retained source",
        url="https://source.example/item",
        dedupe_key=str(uuid.uuid4()),
        content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    return item


def owner_team(db):
    owner = User(
        email=f"migration-{uuid.uuid4()}@example.com",
        password_hash="migration-test",
        role="analyst",
    )
    group = IAMGroup(key=f"migration-{uuid.uuid4().hex}", name="Migration team")
    db.add_all([owner, group])
    db.flush()
    team = Team(
        key=f"migration-{uuid.uuid4().hex}",
        name="Migration team",
        membership_group_id=group.id,
    )
    db.add(team)
    db.flush()
    return owner, team


def test_expansion_populated_round_trip_preserves_legacy_rows_and_matches_metadata(
    db_session, monkeypatch
):
    # Create through the current mapper before rolling back its generated
    # columns. The retained row then survives as an ordinary legacy item.
    item = source(db_session)
    migrations = [
        _migration(db_session, monkeypatch, revision)
        for revision in (*REVISIONS, *LATER_REVISIONS)
    ]
    for migration in reversed(migrations):
        migration.downgrade()
    db_session.execute(
        text("""INSERT INTO item_ai_enrichments
        (item_id, status, source_hash, summary_text, relevance_reasons_json)
        VALUES (:item, 'ready', 'legacy-hash', 'Retained legacy summary', '[]')"""),
        {"item": item.id},
    )
    provider = AIProviderConfiguration(
        name="Legacy compatible provider",
        normalized_name="legacy compatible provider",
        base_url="https://provider.example/v1",
        model="legacy-model",
    )
    db_session.add(provider)
    db_session.flush()
    db_session.execute(
        text("""INSERT INTO ai_provider_routing (singleton_key, default_provider_id)
        VALUES (1, :provider) ON CONFLICT (singleton_key) DO UPDATE SET default_provider_id = :provider"""),
        {"provider": provider.id},
    )
    for migration in migrations:
        migration.upgrade()
    enrichment = db_session.get(ItemAIEnrichment, item.id)
    assert (
        enrichment.summary_text == "Retained legacy summary"
        and enrichment.status == "ready"
    )
    assert enrichment.extraction_progress_json is None
    routing = db_session.get(AIProviderRouting, 1)
    assert (
        routing.default_provider_id == provider.id
        and routing.team_assessment_provider_id is None
    )
    assert (
        compare_metadata(
            MigrationContext.configure(db_session.connection()), Base.metadata
        )
        == []
    )
    for migration in reversed(migrations):
        migration.downgrade()
    for migration in migrations:
        migration.upgrade()
    assert (
        db_session.scalar(
            text("SELECT summary_text FROM item_ai_enrichments WHERE item_id=:item"),
            {"item": item.id},
        )
        == "Retained legacy summary"
    )
    assert (
        compare_metadata(
            MigrationContext.configure(db_session.connection()), Base.metadata
        )
        == []
    )


def test_checkpoint_downgrade_requires_explicit_clear_and_accepts_json_null(
    db_session, monkeypatch
):
    item = source(db_session)
    row = ItemAIEnrichment(
        item_id=item.id,
        extraction_progress_json={"tokens_charged": 1000, "sections": ["complete"]},
    )
    db_session.add(row)
    db_session.flush()
    migration = _migration(db_session, monkeypatch, REVISIONS[0])
    with pytest.raises(RuntimeError, match="paid section progress"):
        migration.downgrade()
    assert (
        db_session.get(ItemAIEnrichment, item.id).extraction_progress_json[
            "tokens_charged"
        ]
        == 1000
    )
    row.extraction_progress_json = None
    db_session.flush()
    _round_trip_legacy(db_session, monkeypatch, REVISIONS[0])


@pytest.mark.parametrize("completed", [False, True])
def test_quota_downgrade_preserves_reservation_attribution_without_current_members(
    db_session, monkeypatch, completed
):
    row = AIProviderBudgetReservation(
        provider_key="legacy",
        quota_group_key=f"account:{uuid.uuid4()}",
        team_key="shared",
        reserved_tokens=1000,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        completed_at=datetime.now(timezone.utc) if completed else None,
        charged_tokens=900 if completed else None,
    )
    db_session.add(row)
    db_session.flush()
    migration = _migration(db_session, monkeypatch, REVISIONS[2])
    with pytest.raises(DBAPIError, match="reservation attribution"):
        with db_session.connection().begin_nested():
            migration.downgrade()
    db_session.refresh(row)
    assert row.quota_group_key and row.reserved_tokens == 1000
    row.quota_group_key = None  # Explicit fixture-only archival/clear.
    db_session.flush()
    _round_trip_legacy(db_session, monkeypatch, REVISIONS[2])


def test_receiver_and_publication_history_require_explicit_removal(
    db_session, monkeypatch
):
    owner, team = owner_team(db_session)
    item = source(db_session)
    event = IntegrationEvent(
        event_type="intel.extraction.ready",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"migration:{uuid.uuid4()}",
        payload_json={},
    )
    db_session.add(event)
    db_session.flush()
    receipt = AutomationExecution(
        webhook_id=uuid.uuid4(),
        owner_user_id=owner.id,
        event_id=event.id,
        action_id="retained-action",
        status="completed",
        findings="Retained findings",
    )
    publication = IndicatorPublication(
        team_id=team.id,
        idempotency_key=uuid.uuid4(),
        request_digest="a" * 64,
        format="stix",
        marking="tlp:green",
        snapshot_json={},
        indicator_count=1,
    )
    db_session.add_all([receipt, publication])
    db_session.flush()
    for index, message in (
        (1, "receiver receipts"),
        (4, "withdrawal and access history"),
    ):
        with pytest.raises(RuntimeError, match=message):
            _migration(db_session, monkeypatch, REVISIONS[index]).downgrade()
    assert (
        db_session.get(AutomationExecution, receipt.id).findings == "Retained findings"
    )
    assert db_session.get(IndicatorPublication, publication.id).indicator_count == 1
    db_session.delete(receipt)
    db_session.delete(publication)
    db_session.flush()
    for index in (4, 1):
        _round_trip_legacy(db_session, monkeypatch, REVISIONS[index])


def test_hunt_claim_downgrade_requires_release(db_session, monkeypatch):
    owner, team = owner_team(db_session)
    item = source(db_session)
    assessment = TeamItemAssessment(
        team_id=team.id,
        item_id=item.id,
        context_version=1,
        source_version=1,
        principal_id=owner.id,
        authorization_encrypted={},
    )
    db_session.add(assessment)
    db_session.flush()
    claim = TeamHuntClaim(
        assessment_id=assessment.id, hunt_id="retained-hunt", owner_user_id=owner.id
    )
    db_session.add(claim)
    db_session.flush()
    migration = _migration(db_session, monkeypatch, REVISIONS[3])
    with pytest.raises(DBAPIError, match="Release team hunt claims"):
        with db_session.connection().begin_nested():
            migration.downgrade()
    db_session.refresh(claim)
    assert claim.owner_user_id == owner.id
    claim.owner_user_id = None
    db_session.flush()
    _round_trip_legacy(db_session, monkeypatch, REVISIONS[3])


def test_alembic_check_matches_complete_head_metadata(database_engine):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option(
        "sqlalchemy.url", database_engine.url.render_as_string(hide_password=False)
    )
    command.check(config)
