from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from app.api.routes import ai_provider_usage as routes
from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_READ_ITEMS
from app.models.ai_usage_event import AIUsageEvent
from app.models.api_token import ApiToken
from app.models.data_policy import (
    DataAccessEnvelope, DataAccessEnvelopeLabel, DataAccessEnvelopeSource, DataPolicyState,
    QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.services.ai_provider_usage import list_provider_usage
from app.services.ai_statistics import build_ai_statistics
from app.services.data_access_policy import DataAccessContext


@pytest.fixture(autouse=True)
def enable_ai(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()


def access(db_session, *, enforced=False):
    state = db_session.get(DataPolicyState, 1)
    return DataAccessContext(mode="enforced" if enforced else "disabled", policy_revision=state.revision,
                             coverage_version=1, principal_type="user", principal_id=uuid.uuid4(), principal_eligible=True,
                             allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}))


def usage(db_session, **changes):
    fields = dict(feature_type="connection_test", success=True, data_access_scope="system", model="shared-model",
                  provider_id=uuid.UUID(int=1), provider_name="Alpha", provider_version=1,
                  prompt_tokens=10, completion_tokens=5, total_tokens=15, latency_ms=10,
                  provider_io_outcome="response_received", created_at=datetime.now(timezone.utc))
    row = AIUsageEvent(**(fields | changes))
    db_session.add(row)
    db_session.flush()
    return row


def test_provider_identity_version_model_and_historical_legacy_groups_remain_separate(db_session):
    usage(db_session)
    usage(db_session, provider_id=uuid.UUID(int=2), provider_name="Deleted Beta")
    usage(db_session, provider_version=2, provider_name="Renamed Alpha")
    usage(db_session, model="second-model")
    usage(db_session, provider_id=None, provider_version=None, provider_name="Legacy settings")
    usage(db_session, provider_id=None, provider_version=None, provider_name=None)
    # The snapshots intentionally have no catalog rows or FK. Deleting a profile
    # therefore cannot remove its recorded identity from this projection.
    result = list_provider_usage(db_session, data_access=access(db_session), limit=100)
    assert result.total == 6
    assert {row.provider_name for row in result.items} == {
        "Alpha", "Deleted Beta", "Renamed Alpha", "Legacy settings", "Unknown historical provider",
    }
    assert len([row for row in result.items if row.provider_id == uuid.UUID(int=1)]) == 3
    assert len([row for row in result.items if row.model == "shared-model"]) == 5


def test_provider_metrics_keep_token_nulls_success_latency_and_typed_failure_categories(db_session):
    usage(db_session, latency_ms=10)
    usage(db_session, latency_ms=30)
    usage(db_session, success=False, total_tokens=None, prompt_tokens=None, completion_tokens=None, latency_ms=999,
          failure_category="dns_deadline", provider_io_outcome="ambiguous")
    usage(db_session, success=False, total_tokens=None, latency_ms=0, failure_category="provider_hourly_token_budget",
          provider_io_outcome="not_sent")
    usage(db_session, success=False, failure_category="future_arbitrary_category", error="Misleading timeout auth 401 text")
    result = list_provider_usage(db_session, data_access=access(db_session))
    row = result.items[0]
    assert row.total_requests == 5 and row.successful_requests == 2 and row.failed_requests == 3
    assert row.success_rate_pct == 40
    assert row.total_tokens == 45 and row.prompt_tokens == 40 and row.completion_tokens == 20
    assert row.unknown_token_requests == 2
    assert row.average_latency_ms == 20 and row.p95_latency_ms == 29
    assert row.deadline_failures == 1 and row.not_sent_requests == 1 and row.ambiguous_requests == 1
    assert row.failure_categories == {"dns_deadline": 1, "provider_hourly_token_budget": 1, "unclassified": 1}


def test_provider_pagination_has_explicit_total_and_deterministic_boundaries(db_session):
    for i in range(32):
        usage(db_session, provider_id=uuid.UUID(int=i + 1), provider_name="Same name")
    context = access(db_session)
    first = list_provider_usage(db_session, data_access=context, limit=25)
    last = list_provider_usage(db_session, data_access=context, limit=25, offset=25)
    empty = list_provider_usage(db_session, data_access=context, limit=25, offset=100)
    assert first.total == last.total == empty.total == 32
    assert len(first.items) == 25 and len(last.items) == 7 and empty.items == []
    assert {row.provider_id for row in first.items}.isdisjoint(row.provider_id for row in last.items)
    assert first.items[0].provider_id == uuid.UUID(int=1)
    assert last.items[-1].provider_id == uuid.UUID(int=32)


def test_provider_usage_filters_time_before_aggregates_and_retains_unmeasured_latency(db_session):
    usage(db_session, created_at=datetime.now(timezone.utc) - timedelta(days=40), total_tokens=999999)
    usage(db_session, success=False, latency_ms=None, total_tokens=None, failure_category="provider_auth")
    row = list_provider_usage(db_session, data_access=access(db_session), days=1).items[0]
    assert row.total_requests == 1 and row.total_tokens == 0
    assert row.average_latency_ms is None and row.p95_latency_ms is None
    assert row.failure_categories == {"provider_auth": 1}


def test_provider_usage_permission_predicate_applies_to_every_group_total_and_category(db_session):
    usage(db_session, provider_name="System connection")
    for label_id, name in [(UNRESTRICTED_HANDLING_LABEL_ID, "Allowed report"), (QUARANTINE_HANDLING_LABEL_ID, "Hidden report")]:
        row = usage(db_session, feature_type="report", data_access_scope="governed", provider_name=name,
                    success=False, failure_category="provider_auth", total_tokens=123456)
        envelope = DataAccessEnvelope(resource_type="ai_usage_event", resource_id=row.id, source_count=1, policy_revision=1)
        db_session.add(envelope)
        db_session.flush()
        db_session.add(DataAccessEnvelopeLabel(envelope_id=envelope.id, label_id=label_id, source_count=1))
        db_session.add(DataAccessEnvelopeSource(envelope_id=envelope.id, source_type="item", source_id=str(uuid.uuid4()),
                                                source_version="1", handling_label_id=label_id, captured_policy_revision=1))
    usage(db_session, feature_type="report", data_access_scope="governed", provider_name="Missing envelope")
    db_session.flush()
    context = access(db_session, enforced=True)
    result = list_provider_usage(db_session, data_access=context)
    assert result.total == 2
    assert {row.provider_name for row in result.items} == {"System connection", "Allowed report"}
    assert sum(row.total_tokens for row in result.items) == 123471
    assert list_provider_usage(db_session, data_access=replace(context, principal_eligible=False)).total == 0


def test_provider_usage_materializes_only_count_and_requested_group_page(db_session):
    db_session.add_all([AIUsageEvent(feature_type="connection_test", success=True, data_access_scope="system",
                                    provider_name="Single group", model="same-model", latency_ms=i) for i in range(2000)])
    db_session.flush()
    returned = []
    loaded = []
    connection = db_session.connection()
    def record(_connection, cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            returned.append(cursor.rowcount)
    def load(*_args):
        loaded.append(True)
    event.listen(connection, "after_cursor_execute", record)
    event.listen(AIUsageEvent, "load", load)
    try:
        result = list_provider_usage(db_session, data_access=access(db_session), limit=1)
    finally:
        event.remove(connection, "after_cursor_execute", record)
        event.remove(AIUsageEvent, "load", load)
    assert result.total == 1 and result.items[0].total_requests == 2000
    assert max(returned) == 1 and loaded == []


def test_provider_usage_route_requires_admin_and_read_ai_scope(client, auth_headers, db_session, seed_users):
    for role in ("viewer", "analyst"):
        assert client.get("/ai/ops/providers", headers=auth_headers[role]).status_code == 403
    token = db_session.scalar(select(ApiToken).where(ApiToken.user_id == seed_users["admin"].id))
    token.scopes = [SCOPE_READ_AI]
    db_session.commit()
    response = client.get("/ai/ops/providers?days=7&limit=2&offset=50", headers=auth_headers["admin"])
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "days": 7, "limit": 2, "offset": 50}
    token.scopes = [SCOPE_READ_ITEMS]
    db_session.commit()
    assert client.get("/ai/ops/providers", headers=auth_headers["admin"]).status_code == 403


@pytest.mark.parametrize("query", ["days=0", "days=366", "limit=0", "limit=101", "offset=-1"])
def test_provider_usage_route_validates_bounded_pagination(client, auth_headers, query):
    assert client.get(f"/ai/ops/providers?{query}", headers=auth_headers["admin"]).status_code == 422


def test_provider_usage_route_refences_policy_after_aggregation(client, auth_headers, monkeypatch):
    from fastapi import HTTPException
    def changed(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail="Authorization changed")
    monkeypatch.setattr(routes, "refence_ai_context", changed)
    result = client.get("/ai/ops/providers", headers=auth_headers["admin"])
    assert result.status_code == 409
    assert "items" not in result.json()


def test_provider_usage_route_handles_database_deadline_without_leaking_partial_results(client, auth_headers, monkeypatch):
    from app.db.budgets import DatabaseDeadlineExceeded
    def unavailable(*_args, **_kwargs):
        raise DatabaseDeadlineExceeded("private SQL")
    monkeypatch.setattr(routes, "list_provider_usage", unavailable)
    response = client.get("/ai/ops/providers", headers=auth_headers["admin"])
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert "private SQL" not in response.text


def test_endpoint_health_uses_typed_deadlines_and_auth_instead_of_english_error_fragments(db_session):
    from app.services.ai_ops_metrics import _build_endpoint_health
    now = datetime.now(timezone.utc)
    usage(db_session, success=False, failure_category="dns_deadline", error="DNS lifetime exhausted", created_at=now - timedelta(minutes=3))
    usage(db_session, success=False, failure_category="total_deadline", error="Request lifetime exhausted", created_at=now - timedelta(minutes=2))
    usage(db_session, success=False, failure_category="provider_auth", error="Credentials declined", created_at=now - timedelta(minutes=1))
    usage(db_session, success=False, failure_category=None, error="timeout auth 401 misleading historical text", created_at=now)
    health = _build_endpoint_health(db_session, since=now - timedelta(days=1), now=now, data_access=access(db_session))
    assert health.timeout_failures == 2
    assert health.last_auth_error == "Credentials declined"
    assert health.last_provider_error == "timeout auth 401 misleading historical text"


def test_provider_usage_route_records_would_deny_evidence_in_audit_mode(client, auth_headers, db_session, seed_users):
    from app.api.deps import get_data_access_context
    from app.main import app
    from app.models.audit_log import AuditLog

    # Missing governed lineage is denied in enforcement and explicitly audited
    # when an administrator evaluates the policy in audit mode.
    usage(db_session, feature_type="report", data_access_scope="governed", provider_name="Audit-only source")
    db_session.commit()
    context = replace(access(db_session), mode="audit", principal_id=seed_users["admin"].id)
    previous = app.dependency_overrides.get(get_data_access_context)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        response = client.get("/ai/ops/providers", headers=auth_headers["admin"])
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    db_session.expire_all()
    entries = db_session.scalars(select(AuditLog).where(AuditLog.action == "data_policy.access.would_deny")).all()
    assert any(entry.metadata_json.get("surface") == "ai.ops.providers.read" for entry in entries)


def test_statistics_bound_time_and_latency_and_keep_missing_usage_distinct(db_session):
    for latency in (999, 1000, 5000, 15000, 60000):
        usage(db_session, latency_ms=latency)
    usage(db_session, success=False, latency_ms=999999, total_tokens=None,
          failure_category="total_deadline", provider_io_outcome="ambiguous")
    usage(db_session, success=False, failure_category="truncated_output", total_tokens=0)
    usage(db_session, success=False, failure_category="provider_hourly_token_budget", provider_io_outcome="not_sent")
    for delta in (timedelta(days=50), timedelta(days=-1)):
        usage(db_session, created_at=datetime.now(timezone.utc) - delta, total_tokens=999999)
    result = build_ai_statistics(db_session, days=7, data_access=access(db_session))
    row = result.features[0]
    assert row.requests == 8 and row.successful == 5 and row.failed == 3
    assert row.known_usage_requests == 7 and row.unknown_usage_requests == 1
    assert row.deadline_failures == row.timeout_failures == row.truncated_outputs == row.budget_rejections == 1
    assert row.not_sent == row.ambiguous == 1
    assert row.latency_samples == 5 and row.p50_latency_ms == 5000
    assert result.latency_histogram == {key: 1 for key in ("under_1s", "1_to_5s", "5_to_15s", "15_to_60s", "60s_or_more")}
    assert result.queues == [] and result.provider_retry_attempts == 0


def test_statistics_refuse_ineligible_principals_and_exclude_missing_lineage(db_session):
    usage(db_session)
    usage(db_session, feature_type="report", data_access_scope="governed", total_tokens=999999)
    context = access(db_session, enforced=True)
    assert sum(row.total_tokens for row in build_ai_statistics(db_session, days=30, data_access=context).features) == 15
    assert build_ai_statistics(db_session, days=30, data_access=replace(context, principal_eligible=False)).features == []


@pytest.mark.parametrize("role,expected", [("admin", 200), ("analyst", 403), ("viewer", 403)])
def test_statistics_route_preserves_admin_boundary(client, auth_headers, role, expected):
    assert client.get("/ai/ops/statistics?days=7", headers=auth_headers[role]).status_code == expected


def test_statistics_refence_and_deadline_fail_closed(client, auth_headers, monkeypatch):
    from app.db.budgets import DatabaseDeadlineExceeded
    from fastapi import HTTPException
    def changed(*args, **kwargs):
        raise HTTPException(409, "Authorization changed")
    monkeypatch.setattr(routes, "refence_ai_context", changed)
    assert client.get("/ai/ops/statistics", headers=auth_headers["admin"]).status_code == 409
    def unavailable(*args, **kwargs):
        raise DatabaseDeadlineExceeded("private SQL")
    monkeypatch.setattr(routes, "build_ai_statistics", unavailable)
    response = client.get("/ai/ops/statistics", headers=auth_headers["admin"])
    assert response.status_code == 503 and response.headers["retry-after"] == "2"
    assert "private SQL" not in response.text
