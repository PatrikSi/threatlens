from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.models.ai_provider_budget import AIProviderBudgetReservation
from app.models.ai_quota_group import AIQuotaGroup, AIQuotaTeamTurn
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_quota_utilization import quota_utilization
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from tests.integration.test_ai_quota_groups import (
    account as account_fixture,
    reserve,
    settle,
)

account = account_fixture


def test_minute_request_limit_counts_settled_and_recovers_after_window(
    database_engine, account
):
    group_id, profiles = account
    with Session(database_engine) as db:
        db.get(AIQuotaGroup, group_id).minute_request_budget = 1
        db.commit()
        first = reserve(db, profiles[0])
        settle(db, first)
        with pytest.raises(AIWorkflowDeferred, match="minute_request_budget"):
            reserve(db, profiles[1])
        db.execute(
            update(AIProviderBudgetReservation)
            .where(AIProviderBudgetReservation.id == first.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(seconds=61))
        )
        db.commit()
        assert reserve(db, profiles[1])


def test_minute_tokens_reconcile_actual_usage_and_reject_oversized_request(
    database_engine, account
):
    group_id, profiles = account
    with Session(database_engine) as db:
        group = db.get(AIQuotaGroup, group_id)
        group.minute_token_budget = 300
        db.commit()
        first = reserve(db, profiles[0])
        settle(db, first, tokens=220)
        with pytest.raises(AIWorkflowDeferred, match="minute_token_budget"):
            reserve(db, profiles[1])
        db.get(AIQuotaGroup, group_id).minute_token_budget = 100
        db.commit()
        with pytest.raises(AIIntegrationError, match="entire token allowance"):
            reserve(db, profiles[1])


def test_team_allocation_does_not_starve_other_teams(database_engine, account):
    group_id, profiles = account
    with Session(database_engine) as db:
        db.get(AIQuotaGroup, group_id).team_hourly_token_budgets = {"team:a": 200}
        db.commit()
        first = reserve(db, profiles[0], "team:a")
        settle(db, first, tokens=150)
        with pytest.raises(AIWorkflowDeferred, match="shared_account_team_hourly"):
            reserve(db, profiles[0], "team:a")
        assert (
            db.get(AIQuotaTeamTurn, (group_id, "team:a")).last_denial_reason
            == "shared_account_team_hourly_token_budget"
        )
        assert reserve(db, profiles[1], "team:b")


def test_utilization_separates_reserved_reported_and_waiting(database_engine, account):
    group_id, profiles = account
    with Session(database_engine) as db:
        first = reserve(db, profiles[0], "team:a")
        with pytest.raises(AIWorkflowDeferred):
            reserve(db, profiles[1], "team:b")
        result = quota_utilization(db, group_id)
        assert result.totals.active_requests == 1
        assert result.totals.reserved_tokens_last_hour > 128
        assert result.totals.reported_tokens_last_hour == 0
        assert result.oldest_wait_seconds is not None
        assert (
            next(
                row for row in result.teams if row.team_key == "team:b"
            ).deferral_reason
            == "shared_account_concurrency_budget"
        )
        settle(db, first, tokens=5)
        result = quota_utilization(db, group_id)
        assert result.totals.reported_tokens_last_hour == 5
        assert result.totals.conservative_tokens_last_hour == 5


def test_older_quota_updates_preserve_new_limits(client, auth_headers, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    created = client.post(
        "/ai/quota-groups",
        headers=auth_headers["admin"],
        json={
            "name": "Minute policies",
            "provider_keys": ["legacy"],
            "minute_request_budget": 20,
            "minute_token_budget": 10000,
            "team_hourly_token_budgets": {"shared": 5000},
        },
    )
    assert created.status_code == 201, created.text
    old = {
        key: value
        for key, value in created.json().items()
        if key
        not in {
            "id",
            "minute_request_budget",
            "minute_token_budget",
            "team_hourly_token_budgets",
        }
    }
    old["name"] = "Old client rename"
    updated = client.put(
        f"/ai/quota-groups/{created.json()['id']}",
        headers=auth_headers["admin"],
        json=old,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["minute_request_budget"] == 20
    assert updated.json()["team_hourly_token_budgets"] == {"shared": 5000}
