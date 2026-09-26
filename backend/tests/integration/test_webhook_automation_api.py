import hashlib
import hmac
import json
import uuid
import pytest


from app.models.feed import Feed
from app.models.article import Article
from app.models.item import Item
from app.models.intel_assessment import ItemIntelState
from app.models.integration import IntegrationDelivery
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.webhook_credential import WebhookCredentialProfile
from app.schemas.notification import NotificationWebhookWrite
from app.services.integration_compat import ensure_webhook_integration
from app.services.integration_events import (
    emit_integration_event,
    route_integration_event,
)
from app.services.notification_webhook_storage import (
    build_notification_webhook,
    apply_notification_webhook_updates,
    decrypt_notification_text,
)
from app.services.notification_webhooks import (
    process_notification_webhook_delivery,
    reserve_notification_webhook_delivery_from_saved_request,
)
from app.services import notification_webhook_http


def test_profile_secrets_are_write_only_and_updates_require_baseline(
    client, db_session, auth_headers, seed_users
):
    response = client.post(
        "/notifications/credential-profiles",
        headers=auth_headers["analyst"],
        json={
            "name": "SIEM",
            "auth_type": "bearer",
            "auth_secret": "canary-private-key",
            "signing_secret": "canary-signing-secret-32-characters",
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    assert profile["auth_configured"] and profile["signing_configured"]
    assert "canary" not in response.text
    stored = db_session.get(WebhookCredentialProfile, uuid.UUID(profile["id"]))
    assert stored.auth_secret_encrypted != "canary-private-key"
    assert (
        client.get(
            "/notifications/credential-profiles", headers=auth_headers["admin"]
        ).json()
        == []
    )
    assert (
        client.patch(
            f"/notifications/credential-profiles/{profile['id']}",
            headers=auth_headers["analyst"],
            json={"name": "Changed", "auth_type": "bearer"},
        ).status_code
        == 409
    )
    result = client.patch(
        f"/notifications/credential-profiles/{profile['id']}",
        headers=auth_headers["analyst"],
        json={
            "name": "Changed",
            "auth_type": "bearer",
            "expected_revision": profile["revision"],
        },
    )
    assert result.status_code == 200 and result.json()["auth_configured"]
    assert (
        "canary"
        not in client.get(
            "/notifications/credential-profiles", headers=auth_headers["analyst"]
        ).text
    )


def _event(db, *, event_type="intel.extraction.ready"):
    feed = Feed(name="Automation source", url=f"https://source.example/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title="Evidence",
        url="https://source.example/article",
        dedupe_key=str(uuid.uuid4()),
        content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    article = Article(
        item_id=item.id,
        text="Threat intelligence evidence",
        final_url=item.url,
        http_status=200,
    )
    db.add(article)
    db.add(
        ItemIntelState(
            item_id=item.id,
            revision=1,
            source_revision=item.classification_required_version,
        )
    )
    db.flush()
    event = emit_integration_event(
        db,
        event_type=event_type,
        source_type="item",
        source_id=item.id,
        idempotency_key=f"automation-test:{uuid.uuid4()}",
        payload={
            "item_id": str(item.id),
            "source_revision": item.classification_required_version,
            "extraction_revision": 1,
            "article_id": str(article.id),
            "article_retrieved_at": article.retrieved_at.isoformat(),
            "feed_id": str(feed.id),
            "indicators_complete": True,
            "action_id": "source-revision-1",
            "indicators": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "domain",
                    "value": "suspicious-host.net",
                    "role": "malicious_infrastructure",
                    "extraction_confidence": 0.95,
                }
            ],
        },
    )
    db.commit()
    return event


def test_stored_event_preview_explains_filter_without_external_send(
    client, db_session, auth_headers, monkeypatch
):
    event = _event(db_session)
    monkeypatch.setattr(
        notification_webhook_http,
        "send_rendered_notification_request",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("preview sent external request")
        ),
    )
    payload = {
        "name": "SIEM",
        "url_template": "https://siem.example/hunts",
        "event_type": event.event_type,
        "payload_mode": "automation_v1",
        "conditions": {
            "op": "all",
            "conditions": [
                {"field": "maliciousness_confidence", "operator": "gte", "value": 0.9}
            ],
        },
    }
    result = client.post(
        "/notifications/webhooks/preview",
        headers=auth_headers["analyst"],
        json={"event_id": str(event.id), "webhook": payload},
    )
    assert result.status_code == 200, result.text
    assert result.json()["matches"] is False
    assert result.json()["missing_fields"] == ["maliciousness_confidence"]
    assert isinstance(result.json()["automation_payload"]["data"]["indicators"], list)
    samples = client.get(
        "/notifications/webhooks/events",
        headers=auth_headers["analyst"],
        params={"event_type": event.event_type},
    ).json()
    assert samples["events"][0]["id"] == str(event.id)
    assert "indicators" not in samples["events"][0]


def test_legacy_edits_preserve_conditions_and_compatibility_projection(
    db_session, seed_users
):
    payload = NotificationWebhookWrite(
        name="SIEM",
        url_template="https://siem.example/hunts",
        conditions={
            "op": "all",
            "conditions": [{"field": "tag", "operator": "in", "value": ["endpoint"]}],
        },
    )
    webhook = build_notification_webhook(seed_users["analyst"].id, payload)
    db_session.add(webhook)
    db_session.flush()
    instance, subscription = ensure_webhook_integration(db_session, webhook)
    apply_notification_webhook_updates(
        webhook,
        NotificationWebhookWrite(
            name="Old-client rename", url_template="https://siem.example/hunts"
        ),
    )
    ensure_webhook_integration(db_session, webhook)
    assert webhook.conditions_json == payload.conditions.model_dump()
    assert subscription.filter_json["conditions"] == webhook.conditions_json
    assert instance.schema_version == 2


def test_automation_delivery_sends_typed_snapshot_with_native_signature(
    client, db_session, seed_users, auth_headers, monkeypatch
):
    event = _event(db_session)
    profile = client.post(
        "/notifications/credential-profiles",
        headers=auth_headers["analyst"],
        json={
            "name": "SIEM",
            "auth_type": "bearer",
            "auth_secret": "bearer-secret",
            "signing_secret": "s" * 32,
        },
    ).json()
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="SIEM",
            url_template="https://siem.example/hunts",
            event_type=event.event_type,
            payload_mode="automation_v1",
            credential_profile_id=profile["id"],
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    db_session.commit()
    routed = route_integration_event(db_session, event_id=event.id)
    assert len(routed.webhook_delivery_ids) == 1, routed
    import httpx
    from contextlib import contextmanager

    sent = []

    @contextmanager
    def marker_session():
        # The rollback-isolated fixture is invisible to a separate connection.
        # Exercise the real marker implementation on its owning test session.
        yield db_session

    monkeypatch.setattr("app.db.session.SessionLocal", marker_session)

    def handle(request):
        sent.append(request)
        return httpx.Response(202, json={"accepted": True})

    @contextmanager
    def safe_client(**kwargs):
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            yield client

    monkeypatch.setattr(
        notification_webhook_http, "build_safe_http_client", safe_client
    )
    monkeypatch.setattr(
        notification_webhook_http,
        "ensure_runtime_fetchable_url",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_validation.validate_notification_target_url",
        lambda *args: None,
    )
    result = process_notification_webhook_delivery(
        db_session, delivery_id=routed.webhook_delivery_ids[0]
    )
    assert result.result.success, result.result.error
    request = sent[0]
    assert request.headers["Authorization"] == "Bearer bearer-secret"
    assert (
        json.loads(request.content)["data"]["indicators"][0]["extraction_confidence"]
        == 0.95
    )
    canonical = (
        b"v1\n"
        + request.headers["X-ThreatLens-Timestamp"].encode()
        + b"\n"
        + str(event.id).encode()
        + b"\n"
        + request.headers["X-ThreatLens-Attempt-ID"].encode()
        + b"\n"
        + request.content
    )
    assert (
        request.headers["X-ThreatLens-Signature"]
        == "v1=" + hmac.new(b"s" * 32, canonical, hashlib.sha256).hexdigest()
    )
    history = client.get(
        f"/notifications/webhooks/{webhook.id}/deliveries",
        headers=auth_headers["analyst"],
    )
    assert "bearer-secret" not in history.text
    assert "X-ThreatLens-Signature" not in history.text


def test_retry_preserves_event_action_and_live_disabled_profile_stops_send(
    db_session, seed_users, monkeypatch
):
    event = _event(db_session)
    profile = WebhookCredentialProfile(
        user_id=seed_users["analyst"].id, name="SIEM", enabled=True, auth_type="none"
    )
    db_session.add(profile)
    db_session.flush()
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="SIEM",
            url_template="https://siem.example/hunts",
            event_type=event.event_type,
            payload_mode="automation_v1",
            credential_profile_id=profile.id,
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    db_session.commit()
    routed = route_integration_event(db_session, event_id=event.id)
    original = db_session.get(
        NotificationWebhookDelivery, routed.webhook_delivery_ids[0]
    )
    retry = reserve_notification_webhook_delivery_from_saved_request(
        db_session, webhook=webhook, delivery=original
    )
    generic = db_session.get(IntegrationDelivery, retry.integration_delivery_id)
    assert generic.event_id == event.id
    assert generic.payload_json["action_id"] == "source-revision-1"
    assert decrypt_notification_text(retry.rendered_body) == decrypt_notification_text(
        original.rendered_body
    )
    profile.enabled = False
    db_session.commit()
    monkeypatch.setattr(
        notification_webhook_http,
        "send_rendered_notification_request",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("disabled profile sent request")
        ),
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_validation.validate_notification_target_url",
        lambda *_: None,
    )
    result = process_notification_webhook_delivery(db_session, delivery_id=retry.id)
    assert result.result.success is False
    assert "disabled" in result.result.error


def test_superseded_source_is_not_routed_and_preview_explains(
    client, db_session, auth_headers, seed_users
):
    event = _event(db_session)
    item = db_session.get(Item, uuid.UUID(event.payload_json["item_id"]))
    item.classification_required_version += 1
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="SIEM",
            url_template="https://siem.example/hunts",
            event_type=event.event_type,
            payload_mode="automation_v1",
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    db_session.commit()
    assert not route_integration_event(
        db_session, event_id=event.id
    ).webhook_delivery_ids
    response = client.post(
        "/notifications/webhooks/preview",
        headers=auth_headers["analyst"],
        json={
            "event_id": str(event.id),
            "webhook": {
                "name": "Preview",
                "url_template": "https://siem.example/hunts",
                "event_type": event.event_type,
                "payload_mode": "automation_v1",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["matches"] is False
    assert any(
        check["field"] == "current_revision" and not check["matched"]
        for check in response.json()["checks"]
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"auth_type": "bearer", "auth_secret": "sensitive-canary\n"},
        {"name": "\x00", "auth_secret": "sensitive-canary"},
        {"name": "   ", "auth_secret": "sensitive-canary"},
        {"signing_secret": "sensitive-canary" + "a" * 32 + "\ud800"},
    ],
)
def test_profile_validation_never_returns_submitted_secrets(
    client, auth_headers, invalid
):
    result = client.post(
        "/notifications/credential-profiles",
        headers=auth_headers["analyst"],
        content=json.dumps({"name": "Invalid", **invalid}),
    )
    assert result.status_code == 422
    assert "sensitive-canary" not in result.text


def test_webhook_can_be_disabled_after_its_credential_profile_is_disabled(
    client,
    db_session,
    auth_headers,
    seed_users,
):
    profile = WebhookCredentialProfile(
        user_id=seed_users["analyst"].id,
        name="Disabled",
        enabled=False,
        auth_type="none",
    )
    db_session.add(profile)
    db_session.flush()
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="SIEM",
            url_template="https://siem.example/hunts",
            credential_profile_id=profile.id,
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    db_session.commit()
    response = client.patch(
        f"/notifications/webhooks/{webhook.id}",
        headers=auth_headers["analyst"],
        json={
            "name": "SIEM",
            "url_template": "https://siem.example/hunts",
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False
    assert response.json()["credential_profile_id"] == str(profile.id)


@pytest.mark.parametrize("with_profile", [False, True])
def test_freshness_expiring_during_dns_is_rechecked_before_external_io(
    db_session,
    seed_users,
    monkeypatch,
    with_profile,
):
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    import httpx
    from app.services import webhook_conditions

    event = _event(db_session)
    profile = WebhookCredentialProfile(
        user_id=seed_users["analyst"].id, name="Signing", enabled=True, auth_type="none"
    )
    if with_profile:
        db_session.add(profile)
        db_session.flush()
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="Fresh evidence",
            url_template="https://siem.example/hunts",
            event_type=event.event_type,
            payload_mode="automation_v1",
            credential_profile_id=profile.id if with_profile else None,
            conditions={
                "op": "all",
                "conditions": [
                    {"field": "freshness_seconds", "operator": "lte", "value": 60}
                ],
            },
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    ensure_webhook_integration(db_session, webhook)
    db_session.commit()
    delivery_id = route_integration_event(
        db_session, event_id=event.id
    ).webhook_delivery_ids[0]
    clock = {"now": datetime.now(timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(webhook_conditions, "datetime", Clock)

    def delayed_dns(*_args, **_kwargs):
        clock["now"] += timedelta(seconds=120)

    @contextmanager
    def safe_client(**_kwargs):
        def send(_request):
            raise AssertionError("Expired evidence was sent")

        with httpx.Client(transport=httpx.MockTransport(send)) as transport:
            yield transport

    monkeypatch.setattr(
        notification_webhook_http, "ensure_runtime_fetchable_url", delayed_dns
    )
    monkeypatch.setattr(
        notification_webhook_http, "build_safe_http_client", safe_client
    )
    monkeypatch.setattr(
        "app.services.notification_webhook_validation.validate_notification_target_url",
        lambda *_args: None,
    )
    result = process_notification_webhook_delivery(db_session, delivery_id=delivery_id)
    assert not result.result.success
    assert "freshness" in result.result.error


@pytest.mark.parametrize("operation", ["profile", "preview"])
def test_request_credential_revocation_is_rechecked_before_result(
    client, db_session, auth_headers, monkeypatch, operation
):
    from app.models.api_token import ApiToken
    from app.services import webhook_request_authority
    from datetime import datetime, timezone

    event = _event(db_session)
    capture = webhook_request_authority.capture_export_authorization

    def revoke_after_authentication(request, authorization, data_access):
        snapshot = capture(request, authorization, data_access)
        token = db_session.get(ApiToken, snapshot.credential_id)
        token.revoked_at = datetime.now(timezone.utc)
        db_session.flush()
        return snapshot

    monkeypatch.setattr(
        webhook_request_authority,
        "capture_export_authorization",
        revoke_after_authentication,
    )
    if operation == "profile":
        result = client.post(
            "/notifications/credential-profiles",
            headers=auth_headers["analyst"],
            json={"name": "Revoked credential"},
        )
    else:
        result = client.post(
            "/notifications/webhooks/preview",
            headers=auth_headers["analyst"],
            json={
                "event_id": str(event.id),
                "webhook": {
                    "name": "Preview",
                    "url_template": "https://siem.example/hunts",
                    "event_type": event.event_type,
                    "payload_mode": "automation_v1",
                },
            },
        )
    assert result.status_code == 403
    assert "credentials" in result.text
