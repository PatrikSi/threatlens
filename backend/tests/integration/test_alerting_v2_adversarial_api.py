from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.routes import alert_operations as alert_operations_routes
from app.core.security import generate_api_token
from app.models.audit_log import AuditLog
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceMetric
from app.models.api_token import ApiToken
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.services.alert_evaluation import persist_alert_evaluation_intent
from app.services.alert_evaluation_admin import list_alert_occurrence_metrics
from app.services.alert_maintenance import maintain_alert_history
from app.services.data_access_policy import DataAccessContext


def _disabled_data_access(user: User) -> DataAccessContext:
    return DataAccessContext(
        mode="disabled",
        policy_revision=1,
        coverage_version=0,
        principal_type="user",
        principal_id=user.id,
        principal_eligible=True,
        allowed_label_ids=frozenset(),
    )


def _seed_alert_context(db, user: User, *, suffix: str):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name=f"API alert feed {suffix}",
        url=f"https://example.com/api-alert-{suffix}.xml",
        enabled=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=f"api-alert-{suffix}",
        url=f"https://example.com/api-alert/{suffix}",
        canonical_url=f"https://example.com/api-alert/{suffix}",
        title=f"Fortinet API alert {suffix}",
        summary="A source snapshot that requires item-read permission.",
        first_seen_at=now,
        dedupe_key=f"api-alert-{suffix}",
        content_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        status="content_fetched",
    )
    rule = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"API alert rule {suffix}",
        category="threat",
        keywords=["fortinet"],
        enabled=True,
        severity="high",
        revision=1,
        durable_since=now,
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.9,
        scores_json={},
        matched_terms_json={},
        source_hash=hashlib.sha256(f"classification-{suffix}".encode()).hexdigest(),
        rules_version="adversarial-api",
    )
    db.add_all([feed, item, rule, classification])
    db.commit()
    occurrence = AlertOccurrence(
        id=uuid.uuid4(),
        alert_interest_id=rule.id,
        rule_id_snapshot=rule.id,
        owner_user_id=user.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=1,
        item_content_hash=item.content_hash,
        alert_name_snapshot=rule.name,
        alert_category_snapshot=rule.category,
        alert_keywords_snapshot=list(rule.keywords),
        matched_keywords=["fortinet"],
        source_snapshot_json={
            "item": {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary,
            }
        },
        severity_snapshot="high",
    )
    db.add(occurrence)
    db.commit()
    return item, rule, classification, occurrence


def _issue_token(db, user: User, *, scopes: list[str], name: str) -> str:
    token, prefix, token_hash = generate_api_token()
    db.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_snapshot_routes_require_item_scope_without_tightening_v1_alert_routes(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    item, rule, _classification, occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="scope-boundary",
    )
    read_alerts = _issue_token(
        db_session,
        seed_users["viewer"],
        scopes=["read:alerts"],
        name="alerts-without-items",
    )
    read_alerts_and_items = _issue_token(
        db_session,
        seed_users["viewer"],
        scopes=["read:alerts", "read:items"],
        name="alerts-with-items",
    )
    write_alerts = _issue_token(
        db_session,
        seed_users["viewer"],
        scopes=["write:alerts"],
        name="write-alerts-without-items",
    )

    assert client.get("/alerts", headers=_bearer(read_alerts)).status_code == 200
    assert (
        client.patch(
            f"/alerts/{rule.id}",
            json={},
            headers=_bearer(write_alerts),
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/alerts/occurrences/{occurrence.id}/activity",
            headers=_bearer(read_alerts),
        ).status_code
        == 200
    )
    for path in (
        "/alerts/occurrences",
        f"/alerts/occurrences/{occurrence.id}",
    ):
        denied = client.get(path, headers=_bearer(read_alerts))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Insufficient token scope"
    denied_mutation = client.patch(
        f"/alerts/occurrences/{occurrence.id}/lifecycle",
        json={"expected_version": 1, "state": "acknowledged"},
        headers=_bearer(write_alerts),
    )
    assert denied_mutation.status_code == 403
    assert (
        client.get(
            "/alerts/occurrences",
            headers=_bearer(read_alerts_and_items),
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/alerts/occurrences/{occurrence.id}",
            headers=auth_headers["viewer"],
        ).status_code
        == 200
    )

    admin_read_alerts = _issue_token(
        db_session,
        seed_users["admin"],
        scopes=["read:alerts"],
        name="admin-alerts-without-items",
    )
    window = {
        "since": (item.first_seen_at - timedelta(seconds=1)).isoformat(),
        "until": (item.first_seen_at + timedelta(seconds=1)).isoformat(),
        "limit": 10,
    }
    preview = client.post(
        "/alerts/occurrences/reconciliation/preview",
        json=window,
        headers=_bearer(admin_read_alerts),
    )
    assert preview.status_code == 403
    assert preview.json()["detail"] == "Insufficient token scope"
    allowed_preview = client.post(
        "/alerts/occurrences/reconciliation/preview",
        json=window,
        headers=auth_headers["admin"],
    )
    assert allowed_preview.status_code == 200
    admin_write_alerts = _issue_token(
        db_session,
        seed_users["admin"],
        scopes=["write:alerts"],
        name="admin-write-alerts-without-items",
    )
    denied_apply = client.post(
        "/alerts/occurrences/reconciliation/apply",
        json={"preview_token": allowed_preview.json()["preview_token"]},
        headers=_bearer(admin_write_alerts),
    )
    assert denied_apply.status_code == 403
    assert denied_apply.json()["detail"] == "Insufficient token scope"


def test_rule_patch_row_version_protects_semantic_suppression_and_disable_mutations(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    _item, rule, _classification, _occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="rule-revision-conflict",
    )

    updated = client.patch(
        f"/alerts/{rule.id}",
        json={"name": "Updated once", "expected_revision": 1},
        headers=auth_headers["viewer"],
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["row_version"] == 2

    suppression_until = datetime.now(timezone.utc) + timedelta(days=1)
    suppressed = client.patch(
        f"/alerts/{rule.id}",
        json={
            "suppression_until": suppression_until.isoformat(),
            "suppression_reason": "Maintenance",
            "expected_row_version": 2,
        },
        headers=auth_headers["viewer"],
    )
    assert suppressed.status_code == 200
    assert suppressed.json()["revision"] == 2
    assert suppressed.json()["row_version"] == 3

    stale = client.patch(
        f"/alerts/{rule.id}",
        json={"enabled": False, "expected_revision": 2},
        headers=auth_headers["viewer"],
    )
    assert stale.status_code == 409
    assert stale.headers["X-Current-Revision"] == "3"
    assert stale.headers["X-Current-Row-Version"] == "3"
    assert stale.headers["X-Current-Rule-Revision"] == "2"
    assert stale.json()["error"]["code"] == "alert_revision_conflict"
    assert stale.json()["detail"] == {
        "message": (
            "The alert rule changed after it was loaded. Refresh the rule and review "
            "the latest values before saving again."
        ),
        "current_revision": 3,
        "current_row_version": 3,
        "current_rule_revision": 2,
    }

    no_op = client.patch(
        f"/alerts/{rule.id}",
        json={"enabled": True, "expected_row_version": 3},
        headers=auth_headers["viewer"],
    )
    assert no_op.status_code == 200
    assert no_op.json()["revision"] == 2
    assert no_op.json()["row_version"] == 3

    stale_delete = client.delete(
        f"/alerts/{rule.id}",
        params={"expected_row_version": 2},
        headers=auth_headers["viewer"],
    )
    assert stale_delete.status_code == 409
    assert stale_delete.json()["detail"]["current_row_version"] == 3
    db_session.expire_all()
    stored = db_session.get(AlertInterest, rule.id)
    assert stored.name == "Updated once"
    assert stored.enabled is True
    assert stored.revision == 2
    assert stored.row_version == 3
    assert stored.suppression_reason == "Maintenance"


def test_legacy_unversioned_rule_mutations_emit_deprecation_audit_and_telemetry(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    _item, rule, _classification, _occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="unversioned-mutation-audit",
    )
    warnings: list[str] = []

    def _capture_warning(message: str, *args) -> None:
        warnings.append(message % args)

    monkeypatch.setattr(
        "app.api.routes.alerts.logger.warning",
        _capture_warning,
    )

    updated = client.patch(
        f"/alerts/{rule.id}",
        json={"name": "Legacy unversioned update"},
        headers=auth_headers["viewer"],
    )
    assert updated.status_code == 200

    deleted = client.delete(
        f"/alerts/{rule.id}",
        headers=auth_headers["viewer"],
    )
    assert deleted.status_code == 204

    db_session.expire_all()
    compatibility_audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == "alerts.compatibility.unversioned_mutation",
                AuditLog.resource_id == str(rule.id),
            )
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        ).all()
    )
    assert sorted(
        audit.metadata_json["operation"] for audit in compatibility_audits
    ) == ["delete", "update"]
    assert all(
        audit.metadata_json["deprecation"] == "expected_row_version_will_be_required"
        for audit in compatibility_audits
    )
    mutation_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(["alerts.update", "alerts.delete"]),
                AuditLog.resource_id == str(rule.id),
            )
        ).all()
    )
    assert len(mutation_audits) == 2
    assert all(
        audit.metadata_json["expected_row_version_supplied"] is False
        for audit in mutation_audits
    )
    assert len(warnings) == 2
    assert any("operation=update" in warning for warning in warnings)
    assert any("operation=delete" in warning for warning in warnings)


def test_admin_can_inspect_and_replay_dead_letters_with_coded_conflicts(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    item, _rule, classification, _occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="dead-letter-api",
    )
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    request.state = "dead_letter"
    request.completed_at = datetime.now(timezone.utc)
    request.dispatch_claimed_at = None
    request.last_error_code = "evaluation_worker_error"
    request.last_error_message = "Alert evaluation failed unexpectedly."
    request.version = 3
    db_session.add(request)
    db_session.commit()

    assert (
        client.get(
            "/alerts/occurrences/evaluations",
            headers=auth_headers["viewer"],
        ).status_code
        == 403
    )
    listed = client.get(
        "/alerts/occurrences/evaluations",
        params={"states": "dead_letter"},
        headers=auth_headers["admin"],
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["last_error_code"] == "evaluation_worker_error"
    detail = client.get(
        f"/alerts/occurrences/evaluations/{intent.request_id}",
        headers=auth_headers["admin"],
    )
    assert detail.status_code == 200
    assert detail.json()["source"] == "live"
    assert "lease_token" not in detail.json()
    assert "source_snapshot_json" not in detail.json()
    activity = client.get(
        f"/alerts/occurrences/evaluations/{intent.request_id}/activity",
        headers=auth_headers["admin"],
    )
    assert activity.status_code == 200
    assert activity.json()["total"] >= 1

    stale = client.post(
        f"/alerts/occurrences/evaluations/{intent.request_id}/replay",
        json={"expected_version": 2},
        headers=auth_headers["admin"],
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "alert_evaluation_version_conflict"
    assert stale.headers["x-current-version"] == "3"
    assert "current version is 3" in stale.json()["detail"]

    queued: list[uuid.UUID] = []
    monkeypatch.setattr(
        alert_operations_routes,
        "enqueue_alert_evaluation_requests",
        lambda request_ids: queued.extend(request_ids) or True,
    )
    replayed = client.post(
        f"/alerts/occurrences/evaluations/{intent.request_id}/replay",
        json={"expected_version": 3},
        headers=auth_headers["admin"],
    )
    assert replayed.status_code == 202
    assert replayed.json()["request"]["source"] == "live"
    assert replayed.json()["request"]["active_source"] == "replay"
    assert replayed.json()["request"]["state"] == "pending"
    assert replayed.json()["enqueue_failed"] is False
    assert queued == [intent.request_id]

    replay_activity = client.get(
        f"/alerts/occurrences/evaluations/{intent.request_id}/activity",
        headers=auth_headers["admin"],
    )
    assert any(
        row["action"] == "replay_requested" for row in replay_activity.json()["items"]
    )

    missing = client.get(
        f"/alerts/occurrences/evaluations/{uuid.uuid4()}",
        headers=auth_headers["admin"],
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "alert_evaluation_not_found"
    invalid_filter = client.get(
        "/alerts/occurrences/evaluations",
        params={"states": "not-a-state"},
        headers=auth_headers["admin"],
    )
    assert invalid_filter.status_code == 422
    assert invalid_filter.json()["error"]["code"] == "alert_evaluation_filter_invalid"


def test_occurrence_metrics_are_exposed_only_to_their_owner(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    _item, _rule, _classification, open_occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="metric-open",
    )
    _item, _rule, _classification, closed_occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="metric-closed",
    )
    _item, _rule, _classification, aggregated_occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="metric-aggregated-awaiting-delete",
    )
    closed_occurrence.lifecycle_state = "closed"
    closed_occurrence.closed_at = datetime.now(timezone.utc)
    closed_occurrence.closure_disposition = "true_positive"
    aggregated_occurrence.lifecycle_state = "closed"
    aggregated_occurrence.closed_at = datetime.now(timezone.utc)
    aggregated_occurrence.closure_disposition = "true_positive"
    aggregated_occurrence.metrics_aggregated_at = datetime.now(timezone.utc)
    db_session.add_all([closed_occurrence, aggregated_occurrence])
    db_session.commit()
    bucket = datetime.now(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    db_session.execute(
        text("SELECT set_config('threatlens.alert_metric_cohort_write', 'on', true)")
    )
    db_session.add_all(
        [
            AlertOccurrenceMetric(
                bucket_start=bucket,
                owner_user_id=seed_users["viewer"].id,
                severity="high",
                lifecycle_state="closed",
                suppressed=False,
                occurrence_count=7,
            ),
            AlertOccurrenceMetric(
                bucket_start=bucket,
                owner_user_id=seed_users["analyst"].id,
                severity="critical",
                lifecycle_state="closed",
                suppressed=True,
                occurrence_count=11,
            ),
        ]
    )
    db_session.commit()

    viewer = client.get(
        "/alerts/occurrences/metrics",
        headers=auth_headers["viewer"],
    )
    analyst = client.get(
        "/alerts/occurrences/metrics",
        headers=auth_headers["analyst"],
    )
    assert viewer.status_code == 200
    assert analyst.status_code == 200
    viewer_rows = viewer.json()["items"]
    assert sum(row["occurrence_count"] for row in viewer_rows) == 9
    assert (
        sum(
            row["occurrence_count"]
            for row in viewer_rows
            if row["lifecycle_state"] != "closed"
        )
        == 1
    )
    assert (
        sum(
            row["occurrence_count"]
            for row in viewer_rows
            if row["lifecycle_state"] == "closed"
        )
        == 8
    )
    assert viewer.json()["truncated"] is False
    assert [row["occurrence_count"] for row in analyst.json()["items"]] == [11]
    assert {row["owner_user_id"] for row in viewer.json()["items"]} == {
        str(seed_users["viewer"].id)
    }
    assert db_session.scalar(select(AlertOccurrenceMetric.occurrence_count)) in {
        7,
        11,
    }
    assert open_occurrence.metrics_aggregated_at is None

    invalid = client.get(
        "/alerts/occurrences/metrics",
        params={"severities": "urgent"},
        headers=auth_headers["viewer"],
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "alert_metrics_filter_invalid"


def test_metric_partial_day_window_is_utc_stable_before_and_after_rollup(
    db_session,
    seed_users,
):
    _item, _rule, _classification, occurrence = _seed_alert_context(
        db_session,
        seed_users["viewer"],
        suffix="metric-utc-rollup",
    )
    created_at = datetime(2025, 1, 15, 1, 30, tzinfo=timezone.utc)
    occurrence.created_at = created_at
    occurrence.updated_at = created_at
    occurrence.lifecycle_state = "closed"
    occurrence.closed_at = created_at + timedelta(hours=1)
    occurrence.closure_disposition = "true_positive"
    db_session.add(occurrence)
    db_session.commit()

    query_since = created_at.replace(hour=12, minute=0)
    query_until = query_since + timedelta(minutes=1)
    try:
        db_session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))
        before = list_alert_occurrence_metrics(
            db_session,
            owner_user_id=seed_users["viewer"].id,
            data_access=_disabled_data_access(seed_users["viewer"]),
            since=query_since,
            until=query_until,
            severities=[],
            lifecycle_states=[],
            suppressed=None,
            limit=100,
        )
        assert [(row.bucket_start, row.occurrence_count) for row in before.items] == [
            (datetime(2025, 1, 15, tzinfo=timezone.utc), 1)
        ]

        maintenance = maintain_alert_history(
            db_session,
            now=created_at + timedelta(days=10),
            occurrence_retention_days=1,
            metric_retention_days=730,
        )
        assert maintenance.occurrences_aggregated == 1
        assert maintenance.occurrences_deleted == 1

        db_session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))
        after = list_alert_occurrence_metrics(
            db_session,
            owner_user_id=seed_users["viewer"].id,
            data_access=_disabled_data_access(seed_users["viewer"]),
            since=query_since,
            until=query_until,
            severities=[],
            lifecycle_states=[],
            suppressed=None,
            limit=100,
        )
        assert [(row.bucket_start, row.occurrence_count) for row in after.items] == [
            (datetime(2025, 1, 15, tzinfo=timezone.utc), 1)
        ]
    finally:
        db_session.execute(text("SET TIME ZONE 'UTC'"))
