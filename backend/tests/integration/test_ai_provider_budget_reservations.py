import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.orm import Session

from app.db.ai_admission import dispose_provider_admission_engines, provider_admission_engine
from app.models.ai_provider_budget import AIProviderBudgetReservation, AIProviderBudgetState
from app.services import ai_provider_budgets as budgets
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred

MESSAGES = [{"role": "user", "content": "Return JSON."}]


@pytest.fixture
def active(database_engine):
    provider = SimpleNamespace(provider_id=uuid.uuid4(), max_concurrent_requests=1,
        hourly_token_budget=0, request_timeout_seconds=5)
    yield provider
    dispose_provider_admission_engines()
    with Session(database_engine) as cleanup, cleanup.begin():
        key = budgets.provider_budget_key(provider)
        cleanup.execute(delete(AIProviderBudgetReservation).where(AIProviderBudgetReservation.provider_key == key))
        cleanup.execute(delete(AIProviderBudgetState).where(AIProviderBudgetState.provider_key == key))


def reserve(db, active):
    return budgets.reserve_provider_budget(db, active, messages=MESSAGES, requested_tokens=128)


def result(*, total=None, prompt=None, completion=None):
    return AICompletionResult(payload={"ok": True}, provider="openai_compatible", model="synthetic", latency_ms=1,
        total_tokens=total, prompt_tokens=prompt, completion_tokens=completion)


@pytest.mark.parametrize("holders", [1, 2])
def test_admission_does_not_need_a_free_main_pool_connection(database_engine, active, holders):
    limited = create_engine(database_engine.url, pool_size=holders, max_overflow=0, pool_timeout=0.1)
    active.max_concurrent_requests = holders
    barrier = threading.Barrier(holders)

    def occupy_and_reserve():
        with Session(limited) as main:
            main.execute(text("SELECT 1"))
            barrier.wait(timeout=5)
            lease = reserve(main, active)
            assert provider_admission_engine(main).pool.size() == 1
            assert main.scalar(text("SELECT 1")) == 1
            return lease

    try:
        with ThreadPoolExecutor(max_workers=holders) as executor:
            leases = list(executor.map(lambda _: occupy_and_reserve(), range(holders)))
        assert len({lease.id for lease in leases}) == holders
    finally:
        limited.dispose()


def test_concurrent_admission_serializes_limit_and_recovers_expired_slots(database_engine, active):
    barrier = threading.Barrier(2)

    def attempt():
        with Session(database_engine) as db:
            barrier.wait(timeout=5)
            try:
                return reserve(db, active)
            except AIWorkflowDeferred as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))
    lease = next(value for value in outcomes if isinstance(value, budgets.AIProviderBudgetLease))
    assert sum(isinstance(value, AIWorkflowDeferred) for value in outcomes) == 1
    with Session(database_engine) as db:
        db.execute(update(AIProviderBudgetReservation).where(AIProviderBudgetReservation.id == lease.id).values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
        assert reserve(db, active).id != lease.id


def test_unknown_usage_remains_charged_and_settlement_is_idempotent(database_engine, active):
    active.hourly_token_budget = 300
    with Session(database_engine) as db:
        lease = reserve(db, active)
        budgets.settle_provider_budget(db, lease.id, result=result())
        budgets.settle_provider_budget(db, lease.id, result=result(total=0))
        stored = db.get(AIProviderBudgetReservation, lease.id)
        assert stored.completed_at is not None and stored.charged_tokens is None
        assert stored.reserved_tokens > 128
        next_lease = reserve(db, active)
        budgets.settle_provider_budget(db, next_lease.id, result=result())
        with pytest.raises(AIWorkflowDeferred):
            reserve(db, active)


def test_not_sent_releases_tokens_and_known_usage_charges_actual_total(database_engine, active):
    active.hourly_token_budget = 200
    with Session(database_engine) as db:
        lease = reserve(db, active)
        budgets.settle_provider_budget(db, lease.id, result=AIIntegrationError("not sent", provider_io_outcome="not_sent"))
        assert db.get(AIProviderBudgetReservation, lease.id).charged_tokens == 0
        next_lease = reserve(db, active)
        budgets.settle_provider_budget(db, next_lease.id, result=result(prompt=2, completion=3))
        assert db.get(AIProviderBudgetReservation, next_lease.id).charged_tokens == 5
        assert reserve(db, active).id != next_lease.id


def test_admission_commits_independently_of_request_rollback_and_never_sends_when_denied(database_engine, active):
    with Session(database_engine) as db:
        db.execute(text("SELECT 1"))
        lease = reserve(db, active)
        db.rollback()
        assert db.get(AIProviderBudgetReservation, lease.id) is not None
        call = Mock()
        with pytest.raises(AIWorkflowDeferred):
            budgets.call_with_provider_budget(db, active, call=call, messages=MESSAGES, requested_tokens=128, call_kwargs={})
        call.assert_not_called()


def test_request_larger_than_hourly_budget_never_reserves_or_calls(database_engine, active):
    active.hourly_token_budget = 1
    with Session(database_engine) as db:
        call = Mock()
        with pytest.raises(AIIntegrationError) as failure:
            budgets.call_with_provider_budget(db, active, call=call, messages=MESSAGES, requested_tokens=128, call_kwargs={})
        assert failure.value.provider_io_outcome == "not_sent"
        call.assert_not_called()
        assert db.scalar(select(AIProviderBudgetReservation.id).where(
            AIProviderBudgetReservation.provider_key == budgets.provider_budget_key(active))) is None


def test_fork_reset_detaches_pool_without_closing_parent_connections(database_engine, active, monkeypatch):
    with Session(database_engine) as db:
        old = provider_admission_engine(db)
        dispose = Mock(wraps=old.dispose)
        monkeypatch.setattr(old, "dispose", dispose)
        dispose_provider_admission_engines(after_fork=True)
        dispose.assert_called_once_with(close=False)
        assert provider_admission_engine(db) is not old


@pytest.mark.parametrize("named", [False, True])
def test_budget_settings_roundtrip_preserve_omission_and_audit_applied_values(client, auth_headers, db_session, monkeypatch, named):
    from app.core.config import get_settings
    from app.models.audit_log import AuditLog

    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    values = {"base_url": "https://example.com/v1", "model": "synthetic", "max_concurrent_requests": 2, "hourly_token_budget": 10000}
    url = "/ai/providers" if named else "/ai/settings"
    if named:
        values["name"] = "Admission limits"
    headers = auth_headers["admin"]
    response = client.post(url, json=values, headers=headers) if named else client.put(url, json=values, headers=headers)
    assert response.status_code in (200, 201), response.text
    saved = response.json()
    assert saved["max_concurrent_requests"] == 2 and saved["hourly_token_budget"] == 10000
    previous_audits = set(db_session.scalars(select(AuditLog.id)).all())
    if named:
        url += f"/{saved['id']}"
        update = {"name": "Admission limits", "version": saved["version"], "base_url": values["base_url"], "model": "changed"}
    else:
        update = {"base_url": values["base_url"], "model": "changed"}
    response = client.put(url, json=update, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["max_concurrent_requests"] == 2 and response.json()["hourly_token_budget"] == 10000
    audit = db_session.scalar(select(AuditLog).where(AuditLog.id.not_in(previous_audits)))
    assert audit.metadata_json["provider_limits"] == {"max_concurrent_requests": 2, "hourly_token_budget": 10000}
    assert "max_concurrent_requests" not in audit.metadata_json.get("changed_fields", [])
    assert "hourly_token_budget" not in audit.metadata_json.get("changed_fields", [])
