"""Expansion upgrades preserve legacy rows and refuse lossy policy rollback."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select, text

from app.models.ai_article_continuation import AIArticleContinuation
from app.models.ai_qualification import AIQualification
from app.models.ai_quota_group import AIQuotaGroup
from app.models.ai_task_run import AITaskRun
from app.models.api_token import ApiToken
from app.models.automation_execution import AutomationExecution
from app.models.automation_receiver import AutomationReceiverCredential
from app.models.integration import IntegrationEvent
from app.models.mcp_oauth import MCPDelegation, MCPOAuthClient
from app.models.notification_webhook import NotificationWebhook
from app.models.publication_consumer import PublicationConsumer
from app.models.team_ai_governance import TeamAIGovernance
from app.models.team_hunt_claim import TeamHuntClaim
from app.models.team_hunt_view import TeamHuntView
from app.models.team_item_assessment import TeamItemAssessment
from app.models.webhook_credential import WebhookCredentialProfile
from tests.unit.test_migration_0107 import _migration
from tests.unit.test_migration_0115 import owner_team, source


def _assert_guard(db, monkeypatch, revision, message, table):
    with pytest.raises(RuntimeError, match=message):
        _migration(db, monkeypatch, revision).downgrade()
    assert db.scalar(text("SELECT to_regclass(:table)"), {"table": table}) == table


@pytest.mark.parametrize("kind", ["destination", "receipt", "receiver", "profile"])
def test_team_integration_rollback_retains_offboarding_history(
    db_session, monkeypatch, kind
):
    owner, team = owner_team(db_session)
    if kind == "destination":
        row = NotificationWebhook(
            user_id=owner.id,
            team_id=team.id,
            name="Team destination",
            url_template="https://receiver.example/hunt",
        )
        message, table = "team integrations or history", "notification_webhooks"
    elif kind == "receipt":
        event = IntegrationEvent(
            event_type="hunt.approved",
            source_type="team",
            idempotency_key=str(uuid.uuid4()),
            payload_json={},
        )
        db_session.add(event)
        db_session.flush()
        row = AutomationExecution(
            owner_user_id=owner.id,
            team_id=team.id,
            webhook_id=uuid.uuid4(),
            event_id=event.id,
            action_id="retained-team-action",
            status="completed",
            findings="Retained reviewed finding",
        )
        message, table = "team integrations or history", "automation_executions"
    elif kind == "receiver":
        row = AutomationReceiverCredential(
            team_id=team.id,
            webhook_id=uuid.uuid4(),
            name="Retained receiver",
            token_hash="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        message, table = "receiver credentials", "automation_receiver_credentials"
    else:
        row = WebhookCredentialProfile(user_id=None, name="Offboarded team profile")
        message, table = (
            "orphaned retained integration history",
            "webhook_credential_profiles",
        )
    db_session.add(row)
    db_session.flush()
    _assert_guard(db_session, monkeypatch, "0116_team_integrations", message, table)
    assert db_session.get(type(row), row.id) is row


def test_team_integration_upgrade_preserves_legacy_destination_and_owner(
    db_session, monkeypatch
):
    owner, _ = owner_team(db_session)
    destination = NotificationWebhook(
        user_id=owner.id,
        name="Legacy destination",
        url_template="https://receiver.example/hunt",
    )
    db_session.add(destination)
    db_session.flush()
    identifier = destination.id
    migration = _migration(db_session, monkeypatch, "0116_team_integrations")
    migration.downgrade()
    assert (
        db_session.scalar(
            text("SELECT user_id FROM notification_webhooks WHERE id=:id"),
            {"id": identifier},
        )
        == owner.id
    )
    migration.upgrade()
    db_session.expire(destination)
    assert destination.user_id == owner.id
    assert destination.team_id is None and destination.ownership_revision == 1
    assert (
        db_session.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname='retain_team_integrations_before_user_delete'"
            )
        )
        == 1
    )


@pytest.mark.parametrize("kind", ["continuation", "qualification"])
def test_paid_work_authorization_history_prevents_lossy_rollback(
    db_session, monkeypatch, kind
):
    owner, _ = owner_team(db_session)
    item = source(db_session)
    task = AITaskRun(
        task_type="enrich_item",
        trigger_source="manual",
        status="queued",
        actor_user_id=owner.id,
        item_id=item.id,
    )
    db_session.add(task)
    db_session.flush()
    shared = dict(
        run_id=task.id,
        request_id=uuid.uuid4(),
        principal_id=owner.id,
        principal_type="user",
        authorization_encrypted={},
        token_budget=1000,
    )
    if kind == "continuation":
        row = AIArticleContinuation(
            **shared,
            item_id=item.id,
            expected_progress_digest="a" * 64,
            section_limit=1,
        )
        revision, message = "0117_article_continuations", "continuation authorizations"
    else:
        row = AIQualification(
            **shared,
            provider_id=uuid.uuid4(),
            provider_version=1,
            features_json=["extraction"],
        )
        revision, message = "0118_ai_qualification", "provider qualifications"
    db_session.add(row)
    db_session.flush()
    _assert_guard(db_session, monkeypatch, revision, message, row.__tablename__)
    assert db_session.get(type(row), task.id).token_budget == 1000


@pytest.mark.parametrize("kind", ["egress", "requests", "tokens", "team_allocation"])
def test_destination_and_capacity_policies_cannot_disappear_on_rollback(
    db_session, monkeypatch, kind
):
    _, team = owner_team(db_session)
    if kind == "egress":
        row = TeamAIGovernance(
            team_id=team.id,
            approved_provider_keys=[],
            label_destinations={},
        )
    else:
        row = AIQuotaGroup(
            name=f"Migration {kind}", normalized_name=f"migration {kind}"
        )
        if kind == "requests":
            row.minute_request_budget = 1
        elif kind == "tokens":
            row.minute_token_budget = 1000
        else:
            row.team_hourly_token_budgets = {str(team.id): 1000}
    db_session.add(row)
    db_session.flush()
    _assert_guard(
        db_session,
        monkeypatch,
        "0119_team_ai_governance",
        "silently weaken egress or capacity policy",
        "team_ai_governance",
    )


@pytest.mark.parametrize("kind", ["view", "schedule"])
def test_hunt_review_rollback_retains_team_workflow(db_session, monkeypatch, kind):
    owner, team = owner_team(db_session)
    if kind == "view":
        row = TeamHuntView(team_id=team.id, name="Oldest pending", filters_json={})
    else:
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
        row = TeamHuntClaim(
            assessment_id=assessment.id,
            hunt_id="retained-review",
            review_version=1,
            priority="urgent",
            review_due_at=datetime.now(timezone.utc),
        )
    db_session.add(row)
    db_session.flush()
    _assert_guard(
        db_session,
        monkeypatch,
        "0120_hunt_review_workflow",
        "Retained hunt review schedules or saved views",
        "team_hunt_views",
    )


def test_publication_registration_blocks_obligation_losing_rollback(
    db_session, monkeypatch
):
    owner, team = owner_team(db_session)
    row = PublicationConsumer(
        team_id=team.id,
        principal_id=owner.id,
        name="Retained registration",
        idempotency_key=uuid.uuid4(),
        request_digest="b" * 64,
        authorization_encrypted={},
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(row)
    db_session.flush()
    _assert_guard(
        db_session,
        monkeypatch,
        "0121_publication_distribution",
        "resolve withdrawal obligations",
        "publication_consumers",
    )


def test_mcp_rollback_requires_revocation_before_removing_audience_boundary(
    db_session, monkeypatch
):
    owner, _ = owner_team(db_session)
    client = MCPOAuthClient(
        name="Migration MCP client", redirect_uris=["https://client.example/callback"]
    )
    token = ApiToken(
        user_id=owner.id,
        name="Delegated MCP token",
        token_hash="a" * 64,
        token_prefix="migration_mcp_" + uuid.uuid4().hex[:12],
        scopes=["read:items"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add_all([client, token])
    db_session.flush()
    db_session.add(
        MCPDelegation(
            token_id=token.id,
            client_id=client.id,
            resource="https://threatlens.example/mcp",
            label_cap_json={"enforced": True, "allowed_label_ids": []},
        )
    )
    db_session.flush()
    _assert_guard(
        db_session,
        monkeypatch,
        "0122_mcp_delegation",
        "Revoke active MCP delegated credentials",
        "mcp_delegations",
    )
    token.revoked_at = datetime.now(timezone.utc)
    db_session.flush()
    migration = _migration(db_session, monkeypatch, "0122_mcp_delegation")
    migration.downgrade()
    migration.upgrade()
    assert db_session.scalar(select(ApiToken.revoked_at).where(ApiToken.id == token.id))
    assert db_session.get(MCPDelegation, token.id) is None
