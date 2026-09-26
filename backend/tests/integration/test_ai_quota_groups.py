"""Account budgets cannot be bypassed through another model/profile or retry."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.ai_provider_budget import (
    AIProviderBudgetReservation,
    AIProviderBudgetState,
)
from app.models.ai_quota_group import AIQuotaGroup, AIQuotaGroupMember, AIQuotaTeamTurn
from app.services import ai_provider_budgets as budgets
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred


@pytest.fixture
def account(database_engine):
    profiles = [
        SimpleNamespace(
            provider_id=uuid.uuid4(),
            max_concurrent_requests=0,
            hourly_token_budget=0,
            request_timeout_seconds=5,
        )
        for _ in range(2)
    ]
    group_id = uuid.uuid4()
    with Session(database_engine) as db, db.begin():
        db.add(
            AIQuotaGroup(
                id=group_id,
                name=str(group_id),
                normalized_name=str(group_id),
                max_concurrent_requests=1,
                hourly_token_budget=0,
                max_concurrent_per_team=1,
            )
        )
        db.flush()
        db.add_all(
            AIQuotaGroupMember(
                group_id=group_id, provider_key=budgets.provider_budget_key(profile)
            )
            for profile in profiles
        )
    yield group_id, profiles
    with Session(database_engine) as db, db.begin():
        keys = [budgets.provider_budget_key(profile) for profile in profiles]
        db.execute(
            delete(AIProviderBudgetReservation).where(
                AIProviderBudgetReservation.provider_key.in_(keys)
            )
        )
        db.execute(delete(AIQuotaGroup).where(AIQuotaGroup.id == group_id))
        db.execute(
            delete(AIProviderBudgetState).where(
                AIProviderBudgetState.provider_key.in_([*keys, f"account:{group_id}"])
            )
        )


def reserve(db, active, team="shared"):
    return budgets.reserve_provider_budget(
        db,
        active,
        messages=[{"role": "user", "content": "Return JSON"}],
        requested_tokens=128,
        team_key=team,
    )


def settle(db, lease, tokens=5):
    budgets.settle_provider_budget(
        db,
        lease.id,
        result=AICompletionResult(
            payload={"ok": True},
            provider="openai_compatible",
            model="fixture",
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=tokens,
        ),
    )


def test_shared_concurrency_applies_to_profiles_with_no_individual_limits(
    database_engine, account
):
    _, profiles = account
    with Session(database_engine) as db:
        first = reserve(db, profiles[0])
        with pytest.raises(
            AIWorkflowDeferred, match="shared_account_concurrency_budget"
        ):
            reserve(db, profiles[1])
        settle(db, first)
        assert reserve(db, profiles[1]).id != first.id


def test_different_profiles_compete_atomically_for_one_account_slot(
    database_engine, account
):
    _, profiles = account
    barrier = threading.Barrier(2)

    def attempt(profile):
        with Session(database_engine) as db:
            barrier.wait(timeout=5)
            try:
                return reserve(db, profile, str(profile.provider_id))
            except AIWorkflowDeferred as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, profiles))
    assert (
        sum(isinstance(value, budgets.AIProviderBudgetLease) for value in results) == 1
    )
    assert sum(isinstance(value, AIWorkflowDeferred) for value in results) == 1


def test_waiting_team_gets_next_turn_and_turn_survives_caller_rollback(
    database_engine, account
):
    group_id, profiles = account
    with Session(database_engine) as db:
        first = reserve(db, profiles[0], "team:a")
        with pytest.raises(AIWorkflowDeferred):
            reserve(db, profiles[1], "team:b")
        db.rollback()
        assert db.get(AIQuotaTeamTurn, (group_id, "team:b")).waiting_until is not None
        settle(db, first)
        with pytest.raises(AIWorkflowDeferred, match="shared_account_team_turn"):
            reserve(db, profiles[0], "team:a")
        second = reserve(db, profiles[1], "team:b")
        settle(db, second)
        assert reserve(db, profiles[0], "team:a")


def test_abandoned_turn_expires_without_provider_side_effect(database_engine, account):
    group_id, profiles = account
    with Session(database_engine) as db:
        first = reserve(db, profiles[0], "team:a")
        with pytest.raises(AIWorkflowDeferred):
            reserve(db, profiles[1], "team:b")
        settle(db, first)
        db.execute(
            update(AIQuotaTeamTurn)
            .where(
                AIQuotaTeamTurn.group_id == group_id,
                AIQuotaTeamTurn.team_key == "team:b",
            )
            .values(waiting_until=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()
        assert reserve(db, profiles[0], "team:a")


def test_account_tokens_and_profile_limits_both_apply(database_engine, account):
    group_id, profiles = account
    with Session(database_engine) as db:
        group = db.get(AIQuotaGroup, group_id)
        group.hourly_token_budget = 200
        db.commit()
        first = reserve(db, profiles[0])
        settle(db, first, 150)
        with pytest.raises(
            AIWorkflowDeferred, match="shared_account_hourly_token_budget"
        ):
            reserve(db, profiles[1])
        profiles[1].hourly_token_budget = 1
        with pytest.raises(AIIntegrationError) as failure:
            reserve(db, profiles[1])
        assert failure.value.provider_io_outcome == "not_sent"


def test_account_inherits_profile_charges_when_membership_changes(
    database_engine, account
):
    group_id, profiles = account
    key = budgets.provider_budget_key(profiles[0])
    with Session(database_engine) as db:
        db.execute(
            delete(AIQuotaGroupMember).where(AIQuotaGroupMember.provider_key == key)
        )
        profiles[0].max_concurrent_requests = 1
        db.commit()
        first = reserve(db, profiles[0])
        settle(db, first, 150)
        group = db.get(AIQuotaGroup, group_id)
        group.hourly_token_budget = 200
        db.add(AIQuotaGroupMember(group_id=group_id, provider_key=key))
        db.commit()
        with pytest.raises(
            AIWorkflowDeferred, match="shared_account_hourly_token_budget"
        ):
            reserve(db, profiles[1])


def test_unknown_outcome_keeps_account_charge_until_window_expires(
    database_engine, account
):
    group_id, profiles = account
    with Session(database_engine) as db:
        db.get(AIQuotaGroup, group_id).hourly_token_budget = 200
        db.commit()
        first = reserve(db, profiles[0])
        budgets.settle_provider_budget(
            db,
            first.id,
            result=AIIntegrationError("uncertain", provider_io_outcome="ambiguous"),
        )
        with pytest.raises(
            AIWorkflowDeferred, match="shared_account_hourly_token_budget"
        ):
            reserve(db, profiles[1])
        assert (
            db.scalar(
                select(AIProviderBudgetReservation.charged_tokens).where(
                    AIProviderBudgetReservation.id == first.id
                )
            )
            is None
        )


def test_profile_denied_team_does_not_hold_the_accounts_next_turn(
    database_engine, account
):
    group_id, profiles = account
    with Session(database_engine) as db:
        db.get(AIQuotaGroup, group_id).max_concurrent_requests = 2
        db.commit()
        profiles[0].max_concurrent_requests = 1
        first = reserve(db, profiles[0], "team:a")
        with pytest.raises(AIWorkflowDeferred, match="provider_concurrency_budget"):
            reserve(db, profiles[0], "team:b")
        assert db.get(AIQuotaTeamTurn, (group_id, "team:b")) is None
        assert reserve(db, profiles[1], "team:c")
        settle(db, first)


def test_quota_administration_permissions_conflicts_and_retry_identity(
    client, auth_headers, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    payload = {
        "id": str(uuid.uuid4()),
        "name": "Shared account",
        "provider_keys": ["legacy"],
        "max_concurrent_requests": 2,
    }
    assert (
        client.post(
            "/ai/quota-groups", json=payload, headers=auth_headers["analyst"]
        ).status_code
        == 403
    )
    created = client.post(
        "/ai/quota-groups", json=payload, headers=auth_headers["admin"]
    )
    assert created.status_code == 201, created.text
    again = client.post("/ai/quota-groups", json=payload, headers=auth_headers["admin"])
    assert again.status_code == 201 and again.json()["version"] == 1
    values = created.json()
    values.pop("id")
    values["max_concurrent_requests"] = 3
    updated = client.put(
        f"/ai/quota-groups/{payload['id']}", json=values, headers=auth_headers["admin"]
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.put(
            f"/ai/quota-groups/{payload['id']}",
            json=values,
            headers=auth_headers["admin"],
        ).status_code
        == 409
    )
    second = client.post(
        "/ai/quota-groups",
        json={**payload, "id": str(uuid.uuid4()), "name": "Another account"},
        headers=auth_headers["admin"],
    )
    assert second.status_code == 409 and "quota_provider_assigned" in second.text


def test_team_routing_inherits_article_until_explicitly_assigned_and_old_clients_preserve_it(
    client, auth_headers, db_session, monkeypatch
):
    from app.core.config import get_settings
    from app.services.ai_config import load_active_ai_settings
    from app.services.ai_ops import queue_ai_task_run

    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    providers = []
    for name in ("Article model", "Team model"):
        created = client.post(
            "/ai/providers",
            json={"name": name, "base_url": "https://ai.example/v1", "model": name},
            headers=auth_headers["admin"],
        )
        assert created.status_code == 201, created.text
        providers.append(created.json())
    routing = client.get("/ai/provider-routing", headers=auth_headers["admin"]).json()
    routing["item_enrichment_provider_id"] = providers[0]["id"]
    routing = client.put(
        "/ai/provider-routing", json=routing, headers=auth_headers["admin"]
    ).json()
    assert (
        str(
            load_active_ai_settings(
                db_session, feature_type="team_assessment"
            ).provider_id
        )
        == providers[0]["id"]
    )
    queued = queue_ai_task_run(
        db_session, task_type="team_assessment", trigger_source="manual"
    )
    db_session.commit()
    routing["team_assessment_provider_id"] = providers[1]["id"]
    routing = client.put(
        "/ai/provider-routing", json=routing, headers=auth_headers["admin"]
    ).json()
    routing.pop("team_assessment_provider_id")
    saved = client.put(
        "/ai/provider-routing", json=routing, headers=auth_headers["admin"]
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["team_assessment_provider_id"] == providers[1]["id"]
    db_session.expire_all()
    assert (
        str(
            load_active_ai_settings(
                db_session, feature_type="team_assessment"
            ).provider_id
        )
        == providers[1]["id"]
    )
    assert (
        str(
            load_active_ai_settings(
                db_session, feature_type="team_assessment", task_run_id=queued.id
            ).provider_id
        )
        == providers[0]["id"]
    )
