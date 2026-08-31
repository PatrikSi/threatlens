from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import delete, func, select

from app.api.deps import get_data_access_context
from app.main import app
from app.models.audit_log import AuditLog
from app.models.ai_daily_brief import AIDailyBrief
from app.models.data_policy import (
    DataPolicyState,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.schemas.integration import SMTPTestResponse
from app.schemas.notification import NotificationWebhookTestResponse
from app.services import notification_webhook_http
from app.services.data_access_policy import DataAccessContext
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DataAccessSourceInput,
    merge_data_access_envelope_sources,
)
from app.services.notification_webhook_test_policy import (
    NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
    NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
    lock_notification_webhook_test_receipt_for_outcome,
)
from app.services.integration_maintenance import (
    prune_integration_delivery_history,
    rollup_terminal_integration_deliveries,
)
from app.services.integration_smtp_hooks import get_smtp_analytics


@contextmanager
def _data_access(
    user_id: uuid.UUID,
    *,
    mode: str = "enforced",
    principal_eligible: bool = True,
) -> Iterator[None]:
    context = _data_access_context(
        user_id,
        mode=mode,
        principal_eligible=principal_eligible,
    )
    previous = app.dependency_overrides.get(get_data_access_context)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous


def _data_access_context(
    user_id: uuid.UUID,
    *,
    mode: str = "enforced",
    principal_eligible: bool = True,
) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,  # type: ignore[arg-type]
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=user_id,
        principal_eligible=principal_eligible,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )


def test_webhook_history_analytics_and_retry_hide_restricted_deliveries(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    visible_feed = Feed(
        name="Visible webhook history",
        url=f"https://example.com/visible-webhook-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Restricted webhook history",
        url=f"https://example.com/restricted-webhook-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    visible_item = _item(visible_feed, "visible-webhook-history")
    restricted_item = _item(restricted_feed, "restricted-webhook-history")
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Policy history webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([visible_item, restricted_item, webhook])
    db_session.flush()
    visible_delivery = _delivery(webhook, user.id, visible_item, visible_feed)
    restricted_delivery = _delivery(
        webhook,
        user.id,
        restricted_item,
        restricted_feed,
    )
    ambiguous_delivery = _delivery(
        webhook,
        user.id,
        visible_item,
        restricted_feed,
    )
    missing_item_delivery = _delivery(
        webhook,
        user.id,
        visible_item,
        visible_feed,
    )
    missing_item_delivery.item_id = None
    feed_failing_delivery = _delivery(
        webhook,
        user.id,
        visible_item,
        visible_feed,
    )
    feed_failing_delivery.item_id = None
    feed_failing_delivery.event_type_snapshot = "feed_failing"
    db_session.add_all(
        [
            visible_delivery,
            restricted_delivery,
            ambiguous_delivery,
            missing_item_delivery,
            feed_failing_delivery,
        ]
    )
    db_session.commit()
    missing_id = uuid.uuid4()

    with _data_access(user.id):
        history = client.get(
            f"/notifications/webhooks/{webhook.id}/deliveries",
            headers=auth_headers["analyst"],
        )
        analytics = client.get(
            "/notifications/analytics",
            headers=auth_headers["analyst"],
        )
        hidden_retry = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{restricted_delivery.id}/retry",
            headers=auth_headers["analyst"],
        )
        missing_retry = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{missing_id}/retry",
            headers=auth_headers["analyst"],
        )

    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert {row["id"] for row in history.json()["deliveries"]} == {
        str(visible_delivery.id),
        str(feed_failing_delivery.id),
    }
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()["total_deliveries"] == 2
    assert analytics.json()["failed_deliveries"] == 2
    assert hidden_retry.status_code == missing_retry.status_code == 404
    assert hidden_retry.json()["detail"] == missing_retry.json()["detail"]
    db_session.expire_all()
    unchanged = db_session.get(NotificationWebhookDelivery, restricted_delivery.id)
    assert unchanged is not None
    assert unchanged.source_delivery_id is None


def test_webhook_retry_refilters_delivery_after_internal_commit(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    feed = Feed(
        name="Retry response relabel feed",
        url=f"https://example.com/retry-relabel-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(feed)
    db_session.flush()
    item = _item(feed, "retry-response-relabel")
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Retry response policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([item, webhook])
    db_session.flush()
    delivery = _delivery(webhook, user.id, item, feed)
    db_session.add(delivery)
    db_session.commit()

    retried_id = uuid.uuid4()

    def _retry_and_relabel(db, *, webhook, delivery):
        retried = _delivery(webhook, user.id, item, feed)
        retried.id = retried_id
        retried.delivery_kind = "retry"
        retried.delivery_state = "succeeded"
        retried.success = True
        retried.status_code = 204
        retried.source_delivery_id = delivery.id
        db.add(retried)
        db.flush()
        locked_feed = db.scalar(
            select(Feed).where(Feed.id == feed.id).with_for_update()
        )
        assert locked_feed is not None
        locked_feed.handling_label_id = QUARANTINE_HANDLING_LABEL_ID
        db.add(locked_feed)
        # Simulate a retry implementation that commits internally before the
        # route serializes its response.
        db.commit()
        return retried

    monkeypatch.setattr(
        "app.api.routes.notifications.retry_notification_webhook_delivery",
        _retry_and_relabel,
    )

    with _data_access(user.id):
        response = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Webhook delivery not found"
    assert str(retried_id) not in response.text


def test_webhook_retry_refences_policy_revision_after_internal_commit(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    feed = Feed(
        name="Retry response revision feed",
        url=f"https://example.com/retry-revision-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(feed)
    db_session.flush()
    item = _item(feed, "retry-response-revision")
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Retry response revision webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([item, webhook])
    db_session.flush()
    delivery = _delivery(webhook, user.id, item, feed)
    db_session.add(delivery)
    db_session.commit()

    def _retry_and_change_revision(db, *, webhook, delivery):
        retried = _delivery(webhook, user.id, item, feed)
        retried.delivery_kind = "retry"
        retried.delivery_state = "succeeded"
        retried.success = True
        retried.status_code = 204
        retried.source_delivery_id = delivery.id
        db.add(retried)
        state = db.get(DataPolicyState, 1)
        assert state is not None
        state.revision += 1
        db.add(state)
        db.commit()
        return retried

    monkeypatch.setattr(
        "app.api.routes.notifications.retry_notification_webhook_delivery",
        _retry_and_change_revision,
    )

    with _data_access(user.id):
        response = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Webhook retry authorization changed. Reload delivery history before "
        "retrying again."
    )


def test_webhook_retry_audit_mode_records_would_deny_before_return(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    feed = Feed(
        name="Audit retry restricted feed",
        url=f"https://example.com/audit-retry-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add(feed)
    db_session.flush()
    item = _item(feed, "audit-retry-restricted")
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Audit retry policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([item, webhook])
    db_session.flush()
    delivery = _delivery(webhook, user.id, item, feed)
    db_session.add(delivery)
    db_session.commit()

    retried_id = uuid.uuid4()

    def _retry(db, *, webhook, delivery):
        retried = _delivery(webhook, user.id, item, feed)
        retried.id = retried_id
        retried.delivery_kind = "retry"
        retried.delivery_state = "succeeded"
        retried.success = True
        retried.status_code = 204
        retried.source_delivery_id = delivery.id
        db.add(retried)
        db.flush()
        return retried

    monkeypatch.setattr(
        "app.api.routes.notifications.retry_notification_webhook_delivery",
        _retry,
    )

    with _data_access(user.id, mode="audit"):
        response = client.post(
            f"/notifications/webhooks/{webhook.id}/deliveries/{delivery.id}/retry",
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(retried_id)
    decision = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.resource_id == str(retried_id),
        )
    )
    assert decision is not None
    assert decision.metadata_json["surface"] == (
        "notifications.webhook.delivery.retry"
    )
    assert "request_served" not in decision.metadata_json
    assert decision.metadata_json["affected_count"] == 1
    assert decision.data_access_label_ids == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]


def test_generic_webhook_history_requires_delivery_envelope_access(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    feed = Feed(
        name="Generic webhook history",
        url=f"https://example.com/generic-webhook-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Generic policy history webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    integration = IntegrationInstance(
        owner_user_id=user.id,
        name="Generic policy webhook integration",
        integration_type="webhook",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add_all([feed, webhook, integration])
    db_session.flush()
    item = _item(feed, "generic-webhook-history")
    db_session.add(item)
    db_session.flush()
    visible_generic = _integration_delivery(
        integration_id=integration.id,
        connector_type="webhook",
    )
    restricted_generic = _integration_delivery(
        integration_id=integration.id,
        connector_type="webhook",
    )
    db_session.add_all([visible_generic, restricted_generic])
    db_session.flush()
    visible = _delivery(webhook, user.id, item, feed)
    visible.integration_delivery_id = visible_generic.id
    restricted = _delivery(webhook, user.id, item, feed)
    restricted.integration_delivery_id = restricted_generic.id
    db_session.add_all([visible, restricted])
    _put_delivery_envelope(
        db_session,
        visible_generic,
        label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    _put_delivery_envelope(
        db_session,
        restricted_generic,
        label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.commit()

    with _data_access(user.id):
        response = client.get(
            f"/notifications/webhooks/{webhook.id}/deliveries",
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [row["id"] for row in response.json()["deliveries"]] == [str(visible.id)]


def test_webhook_audit_history_and_analytics_record_aggregate_would_deny(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    restricted_feed = Feed(
        name="Audit webhook history",
        url=f"https://example.com/audit-webhook-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Audit policy history webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([restricted_feed, webhook])
    db_session.flush()
    restricted_item = _item(restricted_feed, "audit-webhook-history")
    db_session.add(restricted_item)
    db_session.flush()
    delivery = _delivery(webhook, user.id, restricted_item, restricted_feed)
    db_session.add(delivery)
    db_session.commit()

    with _data_access(user.id, mode="audit"):
        history = client.get(
            f"/notifications/webhooks/{webhook.id}/deliveries",
            headers=auth_headers["analyst"],
        )
        analytics = client.get(
            "/notifications/analytics",
            headers=auth_headers["analyst"],
        )

    assert history.status_code == analytics.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["deliveries"][0]["id"] == str(delivery.id)
    assert analytics.json()["total_deliveries"] == 1
    decisions = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "data_policy.access.would_deny")
    ).all()
    by_surface = {row.metadata_json["surface"]: row for row in decisions}
    assert {
        "notifications.webhook.deliveries.read",
        "notifications.analytics.read",
    }.issubset(by_surface)
    assert all(
        by_surface[surface].metadata_json["affected_count"] == 1
        for surface in (
            "notifications.webhook.deliveries.read",
            "notifications.analytics.read",
        )
    )


def test_ineligible_principal_receives_no_webhook_history_or_analytics(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    feed = Feed(
        name="Ineligible webhook history",
        url=f"https://example.com/ineligible-webhook-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Ineligible policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([feed, webhook])
    db_session.flush()
    item = _item(feed, "ineligible-webhook-history")
    db_session.add(item)
    db_session.flush()
    db_session.add(_delivery(webhook, user.id, item, feed))
    db_session.commit()

    with _data_access(user.id, mode="audit", principal_eligible=False):
        history = client.get(
            f"/notifications/webhooks/{webhook.id}/deliveries",
            headers=auth_headers["analyst"],
        )
        analytics = client.get(
            "/notifications/analytics",
            headers=auth_headers["analyst"],
        )

    assert history.status_code == analytics.status_code == 200
    assert history.json()["total"] == 0
    assert analytics.json()["total_deliveries"] == 0


def test_smtp_history_analytics_and_replay_hide_restricted_deliveries(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    hook = IntegrationInstance(
        name="Policy SMTP hook",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(hook)
    db_session.flush()
    visible = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    restricted = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    db_session.add_all([visible, restricted])
    db_session.flush()
    _put_delivery_envelope(
        db_session,
        visible,
        label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    _put_delivery_envelope(
        db_session,
        restricted,
        label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.commit()
    missing_id = uuid.uuid4()

    with _data_access(admin.id):
        history = client.get(
            f"/integrations/smtp/hooks/{hook.id}/deliveries",
            headers=auth_headers["admin"],
        )
        analytics = client.get(
            "/integrations/smtp/analytics",
            headers=auth_headers["admin"],
        )
        hidden_smtp_replay = client.post(
            f"/integrations/smtp/hooks/{hook.id}/deliveries/{restricted.id}/replay",
            headers=auth_headers["admin"],
        )
        missing_smtp_replay = client.post(
            f"/integrations/smtp/hooks/{hook.id}/deliveries/{missing_id}/replay",
            headers=auth_headers["admin"],
        )
        hidden_generic_replay = client.post(
            f"/integrations/deliveries/{restricted.id}/replay",
            headers=auth_headers["admin"],
        )
        missing_generic_replay = client.post(
            f"/integrations/deliveries/{missing_id}/replay",
            headers=auth_headers["admin"],
        )

    assert history.status_code == analytics.status_code == 200
    assert history.json()["total"] == 1
    assert [row["id"] for row in history.json()["deliveries"]] == [str(visible.id)]
    assert analytics.json()["total_deliveries"] == 1
    assert analytics.json()["failed_deliveries"] == 1
    assert hidden_smtp_replay.status_code == missing_smtp_replay.status_code == 404
    assert hidden_smtp_replay.json()["detail"] == missing_smtp_replay.json()["detail"]
    assert (
        hidden_generic_replay.status_code == missing_generic_replay.status_code == 404
    )
    assert (
        hidden_generic_replay.json()["detail"]
        == missing_generic_replay.json()["detail"]
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(IntegrationDelivery)
            .where(IntegrationDelivery.source_delivery_id == restricted.id)
        )
        == 0
    )


def test_smtp_rolled_metrics_use_policy_cohorts_only_when_enforced(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    hook = IntegrationInstance(
        name="Policy SMTP metric hook",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(hook)
    db_session.flush()
    visible = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    restricted = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    db_session.add_all([visible, restricted])
    db_session.flush()
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=visible.id,
        label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        source_type="system",
    )
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=restricted.id,
        label_id=QUARANTINE_HANDLING_LABEL_ID,
        source_type="unresolved",
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_metrics_delay_seconds",
        0,
    )
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    assert rollup_terminal_integration_deliveries(db_session, now=now) == 2

    with _data_access(admin.id):
        enforced = client.get(
            "/integrations/smtp/analytics",
            headers=auth_headers["admin"],
        )
    audit = get_smtp_analytics(
        db_session,
        data_access=_data_access_context(admin.id, mode="audit"),
    )
    disabled = get_smtp_analytics(
        db_session,
        data_access=_data_access_context(admin.id, mode="disabled"),
    )
    ineligible = get_smtp_analytics(
        db_session,
        data_access=_data_access_context(admin.id, principal_eligible=False),
    )
    audit_ineligible = get_smtp_analytics(
        db_session,
        data_access=_data_access_context(
            admin.id,
            mode="audit",
            principal_eligible=False,
        ),
    )

    assert enforced.status_code == 200, enforced.text
    assert enforced.json()["total_deliveries"] == 1
    assert enforced.json()["failed_deliveries"] == 1
    assert audit.total_deliveries == 2
    assert disabled.total_deliveries == 2
    assert ineligible.total_deliveries == 0

    monkeypatch.setattr(
        "app.services.integration_maintenance.settings.integration_delivery_retention_days",
        1,
    )
    retention = prune_integration_delivery_history(
        db_session,
        now=now + timedelta(days=2),
    )
    assert retention["deliveries_deleted"] == 2
    with _data_access(admin.id, mode="audit"):
        audit_response = client.get(
            "/integrations/smtp/analytics",
            headers=auth_headers["admin"],
        )

    assert audit_response.status_code == 200, audit_response.text
    assert audit_response.json()["total_deliveries"] == 2
    metric_decision = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.resource_type == "integration_delivery_metric",
        )
    )
    assert metric_decision is not None
    assert metric_decision.metadata_json["surface"] == (
        "integrations.smtp.analytics.read"
    )
    assert metric_decision.metadata_json["history_scope"] == "metric_cohort"
    assert metric_decision.metadata_json["affected_count"] == 1
    assert metric_decision.data_access_label_ids == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]
    assert audit_ineligible.total_deliveries == 0


def test_smtp_audit_history_analytics_and_replay_record_aggregate_would_deny(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    hook = IntegrationInstance(
        name="Audit policy SMTP hook",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(hook)
    db_session.flush()
    smtp_source = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    generic_source = _integration_delivery(
        integration_id=hook.id,
        connector_type="smtp",
    )
    db_session.add_all([smtp_source, generic_source])
    db_session.flush()
    for delivery in (smtp_source, generic_source):
        _put_delivery_envelope(
            db_session,
            delivery,
            label_id=QUARANTINE_HANDLING_LABEL_ID,
        )
    db_session.commit()
    monkeypatch.setattr(
        "app.api.routes.integrations.enqueue_integration_delivery_processing",
        lambda _delivery_ids: True,
    )

    with _data_access(admin.id, mode="audit"):
        history = client.get(
            f"/integrations/smtp/hooks/{hook.id}/deliveries",
            headers=auth_headers["admin"],
        )
        analytics = client.get(
            "/integrations/smtp/analytics",
            headers=auth_headers["admin"],
        )
        smtp_replay = client.post(
            f"/integrations/smtp/hooks/{hook.id}/deliveries/{smtp_source.id}/replay",
            headers=auth_headers["admin"],
        )
        generic_replay = client.post(
            f"/integrations/deliveries/{generic_source.id}/replay",
            headers=auth_headers["admin"],
        )

    assert history.status_code == analytics.status_code == 200
    assert history.json()["total"] == 2
    assert analytics.json()["total_deliveries"] == 2
    assert smtp_replay.status_code == generic_replay.status_code == 200
    decisions = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "data_policy.access.would_deny")
    ).all()
    by_surface = {row.metadata_json["surface"]: row for row in decisions}
    assert {
        "integrations.smtp.deliveries.read",
        "integrations.smtp.analytics.read",
        "integrations.smtp.delivery.replay",
        "integrations.delivery.replay",
    }.issubset(by_surface)
    assert by_surface["integrations.smtp.deliveries.read"].metadata_json[
        "affected_count"
    ] == 2
    assert by_surface["integrations.smtp.analytics.read"].metadata_json[
        "affected_count"
    ] == 2
    assert by_surface["integrations.smtp.delivery.replay"].metadata_json[
        "affected_count"
    ] == 1
    assert by_surface["integrations.delivery.replay"].metadata_json[
        "affected_count"
    ] == 1


def test_smtp_selected_feeds_are_filtered_and_rejected_without_an_id_oracle(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    visible_feed = Feed(
        name="Visible SMTP configuration",
        url=f"https://example.com/visible-smtp-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Restricted SMTP configuration",
        url=f"https://example.com/restricted-smtp-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.commit()
    missing_feed_id = uuid.uuid4()

    with _data_access(admin.id, mode="disabled"):
        saved = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([visible_feed.id, restricted_feed.id]),
        )
        disabled_missing = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([missing_feed_id]),
        )
    assert saved.status_code == 200, saved.text
    assert saved.json()["feed_ids"] == [
        str(visible_feed.id),
        str(restricted_feed.id),
    ]
    assert disabled_missing.status_code == 422
    assert str(missing_feed_id) in disabled_missing.json()["detail"]

    outbound_calls = 0

    def _unexpected_test(*_args, **_kwargs):
        nonlocal outbound_calls
        outbound_calls += 1
        raise AssertionError("restricted SMTP settings reached test I/O")

    monkeypatch.setattr(
        "app.api.routes.integrations.test_smtp_integration",
        _unexpected_test,
    )
    with _data_access(admin.id):
        listed = client.get(
            "/integrations/smtp/settings", headers=auth_headers["admin"]
        )
        restricted_update = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([restricted_feed.id]),
        )
        missing_update = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([missing_feed_id]),
        )
        restricted_create = client.post(
            "/integrations/smtp/hooks",
            headers=auth_headers["admin"],
            json=_smtp_hook_payload_for_policy(
                "Restricted SMTP hook", [restricted_feed.id]
            ),
        )
        missing_create = client.post(
            "/integrations/smtp/hooks",
            headers=auth_headers["admin"],
            json=_smtp_hook_payload_for_policy("Missing SMTP hook", [missing_feed_id]),
        )
        restricted_test = client.post(
            "/integrations/smtp/test",
            headers=auth_headers["admin"],
            json={"settings": _smtp_settings_payload([restricted_feed.id])},
        )
        missing_test = client.post(
            "/integrations/smtp/test",
            headers=auth_headers["admin"],
            json={"settings": _smtp_settings_payload([missing_feed_id])},
        )
        restricted_hook_test = client.post(
            "/integrations/smtp/hooks/test",
            headers=auth_headers["admin"],
            json={
                "hook": _smtp_hook_payload_for_policy(
                    "Restricted unsaved SMTP hook", [restricted_feed.id]
                )
            },
        )
        missing_hook_test = client.post(
            "/integrations/smtp/hooks/test",
            headers=auth_headers["admin"],
            json={
                "hook": _smtp_hook_payload_for_policy(
                    "Missing unsaved SMTP hook", [missing_feed_id]
                )
            },
        )

    assert listed.status_code == 200, listed.text
    assert listed.json()["feed_ids"] == [str(visible_feed.id)]
    expected_detail = "One or more selected feeds are unavailable"
    for restricted, missing in (
        (restricted_update, missing_update),
        (restricted_create, missing_create),
        (restricted_test, missing_test),
        (restricted_hook_test, missing_hook_test),
    ):
        assert restricted.status_code == missing.status_code == 422
        assert restricted.json()["detail"] == missing.json()["detail"] == expected_detail
        assert str(restricted_feed.id) not in restricted.text
        assert str(missing_feed_id) not in missing.text
    assert outbound_calls == 0


def test_smtp_selected_feed_audit_mode_serves_and_records_aggregate_would_deny(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    admin = seed_users["admin"]
    restricted_feed = Feed(
        name="Audit SMTP configuration",
        url=f"https://example.com/audit-smtp-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add(restricted_feed)
    db_session.commit()

    def _successful_smtp_test(_active_settings, *, recipient_email):
        return SMTPTestResponse(
            success=True,
            action="connection",
            duration_ms=1,
            recipient_email=recipient_email,
            error_code=None,
            error=None,
            server_message="connected",
            tested_at=datetime.now(timezone.utc),
            used_unsaved_settings=True,
        )

    monkeypatch.setattr(
        "app.api.routes.integrations.test_smtp_integration",
        _successful_smtp_test,
    )

    with _data_access(admin.id, mode="audit"):
        updated = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([restricted_feed.id]),
        )
        listed = client.get(
            "/integrations/smtp/settings", headers=auth_headers["admin"]
        )
        hook_created = client.post(
            "/integrations/smtp/hooks",
            headers=auth_headers["admin"],
            json=_smtp_hook_payload_for_policy(
                "Audit restricted SMTP hook", [restricted_feed.id]
            ),
        )
        hooks = client.get(
            "/integrations/smtp/hooks", headers=auth_headers["admin"]
        )
        tested = client.post(
            "/integrations/smtp/test",
            headers=auth_headers["admin"],
            json={"settings": _smtp_settings_payload([restricted_feed.id])},
        )
        hook_tested = client.post(
            "/integrations/smtp/hooks/test",
            headers=auth_headers["admin"],
            json={
                "hook": _smtp_hook_payload_for_policy(
                    "Audit unsaved SMTP hook", [restricted_feed.id]
                )
            },
        )

    assert updated.status_code == listed.status_code == 200
    assert updated.json()["feed_ids"] == listed.json()["feed_ids"] == [
        str(restricted_feed.id)
    ]
    assert hook_created.status_code == 201, hook_created.text
    assert hook_created.json()["feed_ids"] == [str(restricted_feed.id)]
    assert hooks.status_code == 200, hooks.text
    assert tested.status_code == hook_tested.status_code == 200
    assert any(
        hook["id"] == hook_created.json()["id"]
        and hook["feed_ids"] == [str(restricted_feed.id)]
        for hook in hooks.json()
    )
    decisions = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "data_policy.access.would_deny")
    ).all()
    surfaces = {row.metadata_json["surface"] for row in decisions}
    assert {
        "integrations.smtp.settings.update",
        "integrations.smtp.settings.read",
        "integrations.smtp.hook.create",
        "integrations.smtp.hooks.read",
        "integrations.smtp.settings.test_unsaved",
        "integrations.smtp.hook.test_unsaved",
    }.issubset(surfaces)
    for row in decisions:
        if row.metadata_json["surface"] in surfaces:
            assert row.metadata_json["affected_count"] >= 1
            assert row.metadata_json["request_served"] is True
            assert str(restricted_feed.id) not in str(row.metadata_json)


def test_smtp_audit_read_counts_stale_selected_feed_references(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    admin = seed_users["admin"]
    removed_feed = Feed(
        name="Removed audit SMTP feed",
        url=f"https://example.com/removed-audit-smtp-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(removed_feed)
    db_session.commit()
    removed_feed_id = removed_feed.id
    with _data_access(admin.id, mode="audit"):
        saved = client.put(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
            json=_smtp_settings_payload([removed_feed_id]),
        )
    assert saved.status_code == 200, saved.text
    db_session.execute(delete(Feed).where(Feed.id == removed_feed_id))
    db_session.commit()

    with _data_access(admin.id, mode="audit"):
        listed = client.get(
            "/integrations/smtp/settings",
            headers=auth_headers["admin"],
        )

    assert listed.status_code == 200, listed.text
    assert listed.json()["feed_ids"] == [str(removed_feed_id)]
    decision = db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "data_policy.access.would_deny",
            AuditLog.resource_type == "integration_instance",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert decision is not None
    assert decision.metadata_json["surface"] == "integrations.smtp.settings.read"
    assert decision.metadata_json["affected_count"] == 1
    assert decision.metadata_json["unresolved_reference_count"] == 1
    assert decision.data_access_governed is True
    assert decision.data_access_label_ids == []


def test_webhook_test_hides_explicit_restricted_samples_without_outbound_http(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    visible_feed = Feed(
        name="Visible webhook sample",
        url=f"https://example.com/visible-sample-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Restricted webhook sample",
        url=f"https://example.com/restricted-sample-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    visible_item = _item(visible_feed, "visible-explicit-sample")
    restricted_item = _item(restricted_feed, "restricted-explicit-sample")
    db_session.add_all([visible_item, restricted_item])
    db_session.commit()

    outbound_calls = 0

    def _unexpected_send(_rendered):
        nonlocal outbound_calls
        outbound_calls += 1
        raise AssertionError("restricted sample reached outbound HTTP")

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _unexpected_send,
    )
    missing_item_id = uuid.uuid4()
    missing_feed_id = uuid.uuid4()

    with _data_access(user.id):
        restricted_item_response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_item_id": str(restricted_item.id),
                "webhook": _webhook_payload("Restricted item sample"),
            },
            headers=auth_headers["analyst"],
        )
        missing_item_response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_item_id": str(missing_item_id),
                "webhook": _webhook_payload("Missing item sample"),
            },
            headers=auth_headers["analyst"],
        )
        restricted_feed_response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_feed_id": str(restricted_feed.id),
                "webhook": _webhook_payload("Restricted feed sample"),
            },
            headers=auth_headers["analyst"],
        )
        missing_feed_response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_feed_id": str(missing_feed_id),
                "webhook": _webhook_payload("Missing feed sample"),
            },
            headers=auth_headers["analyst"],
        )
        mixed_response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_item_id": str(visible_item.id),
                "sample_feed_id": str(restricted_feed.id),
                "webhook": _webhook_payload("Mixed explicit sample"),
            },
            headers=auth_headers["analyst"],
        )

    assert restricted_item_response.status_code == missing_item_response.status_code == 422
    assert restricted_item_response.json()["detail"] == "Sample item not found"
    assert _error_signature(restricted_item_response) == _error_signature(
        missing_item_response
    )
    assert restricted_feed_response.status_code == missing_feed_response.status_code == 422
    assert restricted_feed_response.json()["detail"] == "Sample feed not found"
    assert _error_signature(restricted_feed_response) == _error_signature(
        missing_feed_response
    )
    assert mixed_response.status_code == 422
    assert mixed_response.json()["detail"] == "Sample feed not found"
    assert outbound_calls == 0


def test_webhook_test_implicit_sample_skips_latest_restricted_item(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    visible_feed = Feed(
        name="Visible implicit feed",
        url=f"https://example.com/visible-implicit-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Restricted implicit feed",
        url=f"https://example.com/restricted-implicit-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    visible_item = _item(visible_feed, "visible-implicit-title")
    visible_item.first_seen_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    restricted_item = _item(restricted_feed, "restricted-implicit-canary")
    restricted_item.first_seen_at = datetime(2030, 1, 2, tzinfo=timezone.utc)
    db_session.add_all([visible_item, restricted_item])
    db_session.commit()

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _successful_send(captured),
    )

    with _data_access(user.id):
        response = client.post(
            "/notifications/webhooks/test",
            json={
                "webhook": _webhook_payload(
                    "Implicit item sample",
                    body_template="{{ item.title }}",
                )
            },
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 200, response.text
    assert captured["calls"] == 1
    assert captured["body"] == visible_item.title
    assert restricted_item.title not in str(captured["body"])


def test_webhook_test_implicit_daily_brief_skips_latest_restricted_brief(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    now = datetime(2030, 2, 2, 9, tzinfo=timezone.utc)
    visible_brief = AIDailyBrief(
        brief_date=date(2030, 2, 1),
        status="ready",
        window_start=now - timedelta(days=2),
        window_end=now - timedelta(days=1),
        generated_at=now - timedelta(days=1),
        title="Visible daily brief",
        brief_text="Visible daily brief body",
        key_points_json=[],
        recommended_actions_json=[],
        top_item_ids_json=[],
        item_count=1,
    )
    restricted_brief = AIDailyBrief(
        brief_date=date(2030, 2, 2),
        status="ready",
        window_start=now - timedelta(days=1),
        window_end=now,
        generated_at=now,
        title="Restricted daily brief canary",
        brief_text="Restricted daily brief body",
        key_points_json=[],
        recommended_actions_json=[],
        top_item_ids_json=[],
        item_count=1,
    )
    db_session.add_all([visible_brief, restricted_brief])
    db_session.flush()
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=visible_brief.id,
        label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=restricted_brief.id,
        label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.commit()

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _successful_send(captured),
    )

    with _data_access(user.id):
        response = client.post(
            "/notifications/webhooks/test",
            json={
                "webhook": _webhook_payload(
                    "Implicit daily brief sample",
                    event_type="daily_digest",
                    body_template="{{ brief.title }}",
                )
            },
            headers=auth_headers["analyst"],
        )

    assert response.status_code == 200, response.text
    assert captured["calls"] == 1
    assert captured["body"] == visible_brief.title
    assert restricted_brief.title not in str(captured["body"])


def test_webhook_test_audit_restricted_source_sends_once_with_durable_evidence(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    restricted_feed = Feed(
        name="Audit restricted webhook sample",
        url=f"https://example.com/audit-sample-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add(restricted_feed)
    db_session.flush()
    restricted_item = _item(restricted_feed, "audit-restricted-sample-title")
    db_session.add(restricted_item)
    db_session.commit()

    operation_id = f"notification-audit-replay-{uuid.uuid4()}"
    captured: dict[str, object] = {}

    def _send(rendered):
        captured["calls"] = int(captured.get("calls", 0)) + 1
        captured["body"] = rendered.body
        captured["pre_io_actions"] = set(
            db_session.scalars(
                select(AuditLog.action).where(
                    AuditLog.request_id == operation_id,
                    AuditLog.action.in_(
                        {
                            NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
                            "data_policy.egress.would_deny",
                        }
                    ),
                )
            ).all()
        )
        notification_webhook_http._mark_notification_external_io_started()
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=1,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="",
            error=None,
        )

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _send,
    )
    request_payload = {
        "sample_item_id": str(restricted_item.id),
        "webhook": _webhook_payload(
            "Audit restricted sample",
            body_template="{{ item.title }}",
        ),
    }
    headers = {**auth_headers["analyst"], "X-Request-ID": operation_id}

    with _data_access(user.id, mode="audit"):
        first = client.post(
            "/notifications/webhooks/test",
            json=request_payload,
            headers=headers,
        )
        replay = client.post(
            "/notifications/webhooks/test",
            json=request_payload,
            headers=headers,
        )

    assert first.status_code == replay.status_code == 200, replay.text
    assert first.json() == replay.json()
    assert captured["calls"] == 1
    assert captured["body"] == restricted_item.title
    assert captured["pre_io_actions"] == {
        NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
        "data_policy.egress.would_deny",
    }

    receipts = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
            AuditLog.request_id == operation_id,
        )
    ).all()
    assert len(receipts) == 1
    receipt = receipts[0]
    outcomes = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
            AuditLog.resource_id == str(receipt.id),
        )
    ).all()
    decisions = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "data_policy.egress.would_deny",
            AuditLog.resource_id == str(receipt.id),
        )
    ).all()
    assert len(outcomes) == len(decisions) == 1
    receipt_metadata = receipt.metadata_json
    assert receipt.actor_principal_id == user.id
    assert receipt.data_access_governed is True
    assert receipt.data_access_label_ids == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]
    assert receipt_metadata["iam_revision"] >= 1
    assert receipt_metadata["data_policy_revision"] == 1
    assert receipt_metadata["data_policy_mode"] == "audit"
    assert receipt_metadata["source_ids"] == {
        "feed_ids": [str(restricted_feed.id)],
        "item_ids": [str(restricted_item.id)],
        "daily_brief_ids": [],
    }
    assert receipt_metadata["handling_label_ids"] == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]
    for digest_key in (
        "destination_digest",
        "request_fingerprint",
        "policy_snapshot_digest",
    ):
        assert len(receipt_metadata[digest_key]) == 64
    assert outcomes[0].metadata_json["state"] == "settled"
    assert outcomes[0].metadata_json["io_outcome"] == "response_received"
    assert outcomes[0].execution_receipt_id == receipt.id
    assert outcomes[0].data_access_governed is True
    assert outcomes[0].data_access_label_ids == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]
    assert decisions[0].metadata_json["decision"] == "egress_would_deny"
    assert "request_served" not in decisions[0].metadata_json


def test_webhook_test_audit_restricted_daily_brief_is_in_final_policy_union(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    now = datetime(2030, 3, 1, 9, tzinfo=timezone.utc)
    brief = AIDailyBrief(
        brief_date=now.date(),
        status="ready",
        window_start=now - timedelta(days=1),
        window_end=now,
        generated_at=now,
        title="Audit restricted daily brief",
        brief_text="Restricted daily brief content",
        key_points_json=[],
        recommended_actions_json=[],
        top_item_ids_json=[],
        item_count=1,
    )
    db_session.add(brief)
    db_session.flush()
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_DAILY_BRIEF,
        resource_id=brief.id,
        label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.commit()

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _successful_send(captured),
    )
    operation_id = f"notification-daily-audit-{uuid.uuid4()}"
    with _data_access(user.id, mode="audit"):
        response = client.post(
            "/notifications/webhooks/test",
            json={
                "webhook": _webhook_payload(
                    "Audit restricted daily brief",
                    event_type="daily_digest",
                    body_template="{{ brief.title }}",
                )
            },
            headers={
                **auth_headers["analyst"],
                "X-Request-ID": operation_id,
            },
        )

    assert response.status_code == 200, response.text
    assert captured["calls"] == 1
    assert captured["body"] == brief.title
    receipt = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
            AuditLog.request_id == operation_id,
        )
    )
    assert receipt is not None
    assert receipt.metadata_json["source_ids"]["daily_brief_ids"] == [
        str(brief.id)
    ]
    assert receipt.metadata_json["handling_label_ids"] == [
        str(QUARANTINE_HANDLING_LABEL_ID)
    ]
    decision = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "data_policy.egress.would_deny",
            AuditLog.resource_id == str(receipt.id),
        )
    )
    assert decision is not None


@pytest.mark.parametrize("mode", ["audit", "enforced"])
def test_webhook_test_missing_real_provenance_before_io_fails_closed(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
    mode,
):
    user = seed_users["analyst"]
    feed = Feed(
        name=f"Missing provenance {mode}",
        url=f"https://example.com/missing-provenance-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(feed)
    db_session.flush()
    item = _item(feed, f"missing-provenance-{mode}")
    db_session.add(item)
    db_session.commit()

    operation_id = f"notification-missing-provenance-{mode}-{uuid.uuid4()}"
    outbound_calls = 0

    def _unexpected_send(_rendered):
        nonlocal outbound_calls
        outbound_calls += 1
        raise AssertionError("missing provenance reached outbound HTTP")

    def _lock_receipt_then_delete_item(db, **kwargs):
        receipt = lock_notification_webhook_test_receipt_for_outcome(db, **kwargs)
        db.execute(delete(Item).where(Item.id == item.id))
        db.flush()
        return receipt

    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _unexpected_send,
    )
    monkeypatch.setattr(
        "app.services.notification_webhooks.lock_notification_webhook_test_receipt_for_outcome",
        _lock_receipt_then_delete_item,
    )
    with _data_access(user.id, mode=mode):
        response = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_item_id": str(item.id),
                "webhook": _webhook_payload("Missing final provenance"),
            },
            headers={
                **auth_headers["analyst"],
                "X-Request-ID": operation_id,
            },
        )

    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == (
        "notification_webhook_test_policy_unavailable"
    )
    assert outbound_calls == 0
    receipt = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_RECEIPT_ACTION,
            AuditLog.request_id == operation_id,
        )
    )
    assert receipt is not None
    outcome = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == NOTIFICATION_WEBHOOK_TEST_OUTCOME_ACTION,
            AuditLog.resource_id == str(receipt.id),
        )
    )
    assert outcome is not None
    assert outcome.metadata_json["state"] == "unavailable"
    assert outcome.metadata_json["io_outcome"] == "not_sent"


def test_webhook_configuration_hides_and_rejects_inaccessible_selected_feeds(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    visible_feed = Feed(
        name="Visible configured feed",
        url=f"https://example.com/visible-config-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Restricted configured feed",
        url=f"https://example.com/restricted-config-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    missing_feed_id = uuid.uuid4()
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Retained policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[
            str(visible_feed.id),
            str(restricted_feed.id),
            str(missing_feed_id),
        ],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.commit()

    with _data_access(user.id):
        listed = client.get(
            "/notifications/webhooks",
            headers=auth_headers["analyst"],
        )
        restricted_create = client.post(
            "/notifications/webhooks",
            json=_webhook_payload(
                "Restricted create",
                feed_ids=[restricted_feed.id],
            ),
            headers=auth_headers["analyst"],
        )
        missing_create = client.post(
            "/notifications/webhooks",
            json=_webhook_payload("Missing create", feed_ids=[missing_feed_id]),
            headers=auth_headers["analyst"],
        )
        restricted_update = client.patch(
            f"/notifications/webhooks/{webhook.id}",
            json=_webhook_payload(
                "Restricted update",
                feed_ids=[restricted_feed.id],
            ),
            headers=auth_headers["analyst"],
        )
        missing_update = client.patch(
            f"/notifications/webhooks/{webhook.id}",
            json=_webhook_payload("Missing update", feed_ids=[missing_feed_id]),
            headers=auth_headers["analyst"],
        )
        visible_create = client.post(
            "/notifications/webhooks",
            json=_webhook_payload("Visible create", feed_ids=[visible_feed.id]),
            headers=auth_headers["analyst"],
        )
        visible_update = client.patch(
            f"/notifications/webhooks/{webhook.id}",
            json=_webhook_payload("Visible update", feed_ids=[visible_feed.id]),
            headers=auth_headers["analyst"],
        )

    assert listed.status_code == 200, listed.text
    retained = next(row for row in listed.json() if row["id"] == str(webhook.id))
    assert retained["feed_ids"] == [str(visible_feed.id)]
    assert str(restricted_feed.id) not in listed.text
    assert str(missing_feed_id) not in listed.text

    expected_error = {"detail": "One or more selected feeds are unavailable"}
    assert restricted_create.status_code == missing_create.status_code == 422
    assert restricted_create.json()["detail"] == expected_error["detail"]
    assert _error_signature(restricted_create) == _error_signature(missing_create)
    assert restricted_update.status_code == missing_update.status_code == 422
    assert restricted_update.json()["detail"] == expected_error["detail"]
    assert _error_signature(restricted_update) == _error_signature(missing_update)
    for rejected in (
        restricted_create,
        missing_create,
        restricted_update,
        missing_update,
    ):
        assert str(restricted_feed.id) not in rejected.text
        assert str(missing_feed_id) not in rejected.text

    assert visible_create.status_code == 201, visible_create.text
    assert visible_create.json()["feed_ids"] == [str(visible_feed.id)]
    assert visible_update.status_code == 200, visible_update.text
    assert visible_update.json()["feed_ids"] == [str(visible_feed.id)]
    db_session.expire_all()
    retained_after_update = db_session.get(NotificationWebhook, webhook.id)
    assert retained_after_update is not None
    assert retained_after_update.feed_ids_json == [
        str(visible_feed.id),
        str(restricted_feed.id),
        str(missing_feed_id),
    ]


def test_webhook_all_hidden_selected_projection_is_read_only_and_preserved(
    client,
    auth_headers,
    db_session,
    seed_users,
):
    user = seed_users["analyst"]
    restricted_feed = Feed(
        name="All-hidden configured feed",
        url=f"https://example.com/all-hidden-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add(restricted_feed)
    db_session.flush()
    webhook = NotificationWebhook(
        user_id=user.id,
        name="All-hidden policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[str(restricted_feed.id)],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    legacy_empty_webhook = NotificationWebhook(
        user_id=user.id,
        name="Legacy-empty policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([webhook, legacy_empty_webhook])
    db_session.commit()

    selected_empty = _webhook_payload("All-hidden policy webhook", feed_ids=[])
    with _data_access(user.id):
        listed = client.get(
            "/notifications/webhooks",
            headers=auth_headers["analyst"],
        )
        preserved = client.patch(
            f"/notifications/webhooks/{webhook.id}",
            json=selected_empty,
            headers=auth_headers["analyst"],
        )
        legacy_empty_preserved = client.patch(
            f"/notifications/webhooks/{legacy_empty_webhook.id}",
            json=selected_empty,
            headers=auth_headers["analyst"],
        )
        rejected_create = client.post(
            "/notifications/webhooks",
            json=_webhook_payload("Selected empty create", feed_ids=[]),
            headers=auth_headers["analyst"],
        )

    assert listed.status_code == 200
    listed_webhook = next(
        row for row in listed.json() if row["id"] == str(webhook.id)
    )
    assert listed_webhook["feed_scope"] == "selected"
    assert listed_webhook["feed_ids"] == []
    assert str(restricted_feed.id) not in listed.text
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()["feed_scope"] == "selected"
    assert preserved.json()["feed_ids"] == []
    assert legacy_empty_preserved.status_code == preserved.status_code
    assert legacy_empty_preserved.json()["feed_scope"] == "selected"
    assert legacy_empty_preserved.json()["feed_ids"] == []
    assert rejected_create.status_code == 422
    assert rejected_create.json()["detail"] == "At least one selected feed is required"

    db_session.expire_all()
    stored = db_session.get(NotificationWebhook, webhook.id)
    assert stored is not None
    assert stored.feed_scope == "selected"
    assert stored.feed_ids_json == [str(restricted_feed.id)]

    all_scope_payload = _webhook_payload("Explicit all scope")
    with _data_access(user.id):
        switched_to_all = client.patch(
            f"/notifications/webhooks/{webhook.id}",
            json=all_scope_payload,
            headers=auth_headers["analyst"],
        )
    assert switched_to_all.status_code == 200, switched_to_all.text
    assert switched_to_all.json()["feed_scope"] == "all"
    assert switched_to_all.json()["feed_ids"] == []
    db_session.expire_all()
    stored = db_session.get(NotificationWebhook, webhook.id)
    assert stored is not None
    assert stored.feed_scope == "all"
    assert stored.feed_ids_json == []


def test_webhook_policy_disabled_preserves_selected_feed_and_sample_behavior(
    client,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch,
):
    user = seed_users["analyst"]
    visible_feed = Feed(
        name="Disabled-mode visible feed",
        url=f"https://example.com/disabled-visible-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name="Disabled-mode restricted feed",
        url=f"https://example.com/disabled-restricted-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    restricted_item = _item(restricted_feed, "disabled-mode-restricted-title")
    webhook = NotificationWebhook(
        user_id=user.id,
        name="Disabled mode policy webhook",
        event_type="rss_item_new",
        url_template="https://example.com/hook",
        method="POST",
        feed_scope="selected",
        feed_ids_json=[str(visible_feed.id), str(restricted_feed.id)],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add_all([restricted_item, webhook])
    db_session.commit()

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.notification_webhook_http.send_rendered_notification_request",
        _successful_send(captured),
    )

    with _data_access(user.id, mode="disabled"):
        listed = client.get(
            "/notifications/webhooks",
            headers=auth_headers["analyst"],
        )
        tested = client.post(
            "/notifications/webhooks/test",
            json={
                "sample_item_id": str(restricted_item.id),
                "webhook": _webhook_payload(
                    "Disabled mode sample",
                    body_template="{{ item.title }}",
                ),
            },
            headers=auth_headers["analyst"],
        )

    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["feed_ids"] == [
        str(visible_feed.id),
        str(restricted_feed.id),
    ]
    assert tested.status_code == 200, tested.text
    assert captured["calls"] == 1
    assert captured["body"] == restricted_item.title


def _smtp_settings_payload(feed_ids: list[uuid.UUID]) -> dict[str, object]:
    return {
        "enabled": False,
        "host": "smtp.example.com",
        "port": 587,
        "security": "starttls",
        "username": None,
        "from_email": "threatlens@example.com",
        "from_name": "ThreatLens",
        "to_emails": ["analyst@example.com"],
        "timeout_seconds": 10,
        "event_types": ["rss_item_new"],
        "feed_scope": "selected",
        "feed_ids": [str(feed_id) for feed_id in feed_ids],
        "subject_template": "[ThreatLens] {{ event.type }}",
        "html_template": "<p>{{ event.type }}</p>",
    }


def _smtp_hook_payload_for_policy(
    name: str, feed_ids: list[uuid.UUID]
) -> dict[str, object]:
    return {
        "name": name,
        "credential_source_id": None,
        "settings": _smtp_settings_payload(feed_ids),
    }


def _webhook_payload(
    name: str,
    *,
    feed_ids: list[uuid.UUID] | None = None,
    event_type: str = "rss_item_new",
    body_template: str | None = None,
) -> dict[str, object]:
    selected_feed_ids = feed_ids if feed_ids is not None else []
    return {
        "name": name,
        "enabled": True,
        "event_type": event_type,
        "url_template": "https://hooks.example.com/policy-test",
        "method": "POST",
        "feed_scope": "selected" if feed_ids is not None else "all",
        "feed_ids": [str(feed_id) for feed_id in selected_feed_ids],
        "query_params": [],
        "headers": [],
        "body_mode": "raw" if body_template is not None else "none",
        "body_fields": [],
        "body_template": body_template,
        "timeout_seconds": 10,
    }


def _error_signature(response) -> dict[str, object]:
    payload = response.json()
    error = dict(payload.get("error") or {})
    error.pop("request_id", None)
    return {"detail": payload.get("detail"), "error": error}


def _successful_send(captured: dict[str, object]):
    def _send(rendered):
        captured["calls"] = int(captured.get("calls", 0)) + 1
        captured["body"] = rendered.body
        return NotificationWebhookTestResponse(
            success=True,
            status_code=204,
            duration_ms=1,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview="",
            error=None,
        )

    return _send


def _item(feed: Feed, key: str) -> Item:
    return Item(
        feed_id=feed.id,
        title=key,
        url=f"https://example.com/{key}",
        dedupe_key=f"{key}:{uuid.uuid4()}",
        content_hash=key.ljust(64, "0")[:64],
    )


def _delivery(
    webhook: NotificationWebhook,
    user_id: uuid.UUID,
    item: Item,
    feed: Feed,
) -> NotificationWebhookDelivery:
    return NotificationWebhookDelivery(
        webhook_id=webhook.id,
        user_id=user_id,
        event_type_snapshot="rss_item_new",
        item_id=item.id,
        feed_id=feed.id,
        delivery_state="failed",
        success=False,
        rendered_url="https://example.com/hook",
        rendered_method="POST",
        rendered_headers_json=[],
        rendered_query_params_json=[],
        error="connection_error: test failure",
    )


def _integration_delivery(
    *,
    integration_id: uuid.UUID,
    connector_type: str,
) -> IntegrationDelivery:
    return IntegrationDelivery(
        integration_id=integration_id,
        connector_type=connector_type,
        event_type="rss_item_new",
        delivery_kind="live",
        state="dead_letter",
        idempotency_key=f"policy-history:{uuid.uuid4()}",
        payload_json={},
        max_attempts=3,
    )


def _put_delivery_envelope(
    db_session,
    delivery: IntegrationDelivery,
    *,
    label_id: uuid.UUID,
) -> None:
    _put_resource_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
        resource_id=delivery.id,
        label_id=label_id,
    )


def _put_resource_envelope(
    db_session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    label_id: uuid.UUID,
    source_type: str = "test_fixture",
) -> None:
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    merge_data_access_envelope_sources(
        db_session,
        resource_type=resource_type,
        resource_id=resource_id,
        sources=(
            DataAccessSourceInput(
                source_type=source_type,
                source_id=str(resource_id),
                source_version="1",
                handling_label_id=label_id,
                captured_policy_revision=state.revision,
            ),
        ),
    )
