import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationRun,
    IntegrationSubscription,
)
from app.schemas.integration import SMTPTestResponse
from app.services.integration_storage import build_active_smtp_settings, get_smtp_credential_source
from app.services.secret_storage import is_encrypted_json


def _smtp_hook_payload(
    name: str,
    *,
    password: str | None = "relay-password",
    credential_source_id: str | None = None,
    event_type: str = "rss_item_new",
) -> dict:
    settings = {
        "enabled": True,
        "host": "smtp.example.com",
        "port": 587,
        "security": "starttls",
        "username": "relay-user",
        "from_email": "threatlens@example.com",
        "from_name": "ThreatLens",
        "to_emails": ["analyst@example.com"],
        "timeout_seconds": 10,
        "event_types": [event_type],
        "feed_scope": "all",
        "feed_ids": [],
        "subject_template": "[ThreatLens] {{ event.type }}",
        "html_template": "<p>{{ event.type }}</p>",
    }
    if password is not None:
        settings["password"] = password
    return {
        "name": name,
        "credential_source_id": credential_source_id,
        "settings": settings,
    }


def test_admin_can_manage_smtp_settings_without_secret_leakage(client: TestClient, auth_headers, db_session):
    connectors_response = client.get("/integrations/connectors", headers=auth_headers["admin"])
    assert connectors_response.status_code == 200
    assert [connector["integration_type"] for connector in connectors_response.json()] == ["smtp", "webhook"]

    viewer_response = client.get("/integrations/smtp/settings", headers=auth_headers["viewer"])
    assert viewer_response.status_code == 403

    update_response = client.put(
        "/integrations/smtp/settings",
        headers=auth_headers["admin"],
        json={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "username": "relay-user",
            "password": "relay-password",
            "from_email": "threatlens@example.com",
            "from_name": "ThreatLens",
            "to_emails": ["analyst@example.com", "soc@example.com"],
            "timeout_seconds": 10,
        },
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["enabled"] is True
    assert payload["to_emails"] == ["analyst@example.com", "soc@example.com"]
    assert payload["password_configured"] is True
    assert "password" not in payload

    instance = db_session.scalar(select(IntegrationInstance).where(IntegrationInstance.integration_type == "smtp"))
    assert instance is not None
    assert is_encrypted_json(instance.secret_json)

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "integrations.smtp.update"))
    assert audit is not None
    assert audit.metadata_json["password_action"] == "updated"
    assert "relay-password" not in str(audit.metadata_json)
    assert audit.metadata_json["recipient_count"] == 2


def test_custom_integration_operator_permission_reaches_read_and_write_routes(
    client: TestClient, auth_headers, seed_users
):
    role_response = client.post(
        "/iam/roles",
        headers=auth_headers["admin"],
        json={
            "key": f"smtp-operator-{uuid.uuid4().hex}",
            "name": "SMTP operator",
            "permissions": ["write:integrations"],
        },
    )
    assert role_response.status_code == 201, role_response.text
    role = role_response.json()
    assignment = client.post(
        f"/iam/users/{seed_users['viewer'].id}/role-assignments",
        headers=auth_headers["admin"],
        json={
            "role_id": role["id"],
            "expected_role_revision": role["revision"],
        },
    )
    assert assignment.status_code == 201, assignment.text

    connectors = client.get(
        "/integrations/connectors", headers=auth_headers["viewer"]
    )
    assert connectors.status_code == 200, connectors.text
    created = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["viewer"],
        json=_smtp_hook_payload("Delegated SMTP destination"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Delegated SMTP destination"


def test_enabled_smtp_requires_recipient_emails(client: TestClient, auth_headers):
    update_response = client.put(
        "/integrations/smtp/settings",
        headers=auth_headers["admin"],
        json={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "from_email": "threatlens@example.com",
            "timeout_seconds": 10,
        },
    )

    assert update_response.status_code == 422
    assert "recipient email" in update_response.json()["detail"]


def test_smtp_test_uses_saved_settings_and_records_health(client: TestClient, auth_headers, monkeypatch):
    client.put(
        "/integrations/smtp/settings",
        headers=auth_headers["admin"],
        json={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "username": "relay-user",
            "password": "relay-password",
            "from_email": "threatlens@example.com",
            "from_name": "ThreatLens",
            "to_emails": ["analyst@example.com"],
            "timeout_seconds": 10,
        },
    )
    captured = {}

    def fake_test(active_settings, *, recipient_email):
        captured["host"] = active_settings.host
        captured["password"] = active_settings.password
        captured["recipient_email"] = recipient_email
        return SMTPTestResponse(
            success=True,
            action="send",
            duration_ms=12,
            recipient_email=recipient_email,
            error_code=None,
            error=None,
            server_message="accepted",
            tested_at=datetime.now(timezone.utc),
            used_unsaved_settings=False,
        )

    monkeypatch.setattr("app.api.routes.integrations.test_smtp_integration", fake_test)

    test_response = client.post(
        "/integrations/smtp/test",
        headers=auth_headers["admin"],
        json={"recipient_email": "analyst@example.com"},
    )
    assert test_response.status_code == 200
    assert test_response.json()["success"] is True
    assert test_response.json()["used_unsaved_settings"] is False
    assert captured == {
        "host": "smtp.example.com",
        "password": "relay-password",
        "recipient_email": "analyst@example.com",
    }

    settings_response = client.get("/integrations/smtp/settings", headers=auth_headers["admin"])
    assert settings_response.status_code == 200
    assert settings_response.json()["health_status"] == "healthy"
    assert settings_response.json()["last_success_at"] is not None

    test_audit = client.get(
        "/audit-logs?action=integrations.smtp.test",
        headers=auth_headers["admin"],
    ).json()["logs"][0]
    assert test_audit["success"] is True
    assert test_audit["metadata_json"] == {
        "run_id": test_audit["metadata_json"]["run_id"],
        "action": "send",
        "duration_ms": 12,
        "error_code": None,
        "error_message": None,
        "server_message": "accepted",
        "recipient_provided": True,
        "used_unsaved_settings": False,
    }
    assert uuid.UUID(test_audit["metadata_json"]["run_id"])


def test_smtp_test_can_use_unsaved_settings_without_mutating_saved_config(client: TestClient, auth_headers, monkeypatch):
    client.put(
        "/integrations/smtp/settings",
        headers=auth_headers["admin"],
        json={
            "enabled": False,
            "host": "saved.example.com",
            "port": 587,
            "security": "starttls",
            "username": None,
            "from_email": "saved@example.com",
            "from_name": "ThreatLens",
            "to_emails": ["saved-recipient@example.com"],
            "timeout_seconds": 10,
        },
    )
    captured = {}

    def fake_test(active_settings, *, recipient_email):
        captured["host"] = active_settings.host
        captured["from_email"] = active_settings.from_email
        captured["password"] = active_settings.password
        captured["recipient_email"] = recipient_email
        return SMTPTestResponse(
            success=True,
            action="connection",
            duration_ms=8,
            recipient_email=recipient_email,
            error_code=None,
            error=None,
            server_message="250 OK",
            tested_at=datetime.now(timezone.utc),
            used_unsaved_settings=False,
        )

    monkeypatch.setattr("app.api.routes.integrations.test_smtp_integration", fake_test)

    test_response = client.post(
        "/integrations/smtp/test",
        headers=auth_headers["admin"],
        json={
            "settings": {
                "enabled": True,
                "host": "draft.example.com",
                "port": 465,
                "security": "ssl_tls",
                "username": "draft-user",
                "password": "draft-password",
                "from_email": "draft@example.com",
                "from_name": "ThreatLens Draft",
                "to_emails": ["draft-recipient@example.com"],
                "timeout_seconds": 5,
            }
        },
    )
    assert test_response.status_code == 200
    assert test_response.json()["used_unsaved_settings"] is True
    assert captured == {
        "host": "draft.example.com",
        "from_email": "draft@example.com",
        "password": "draft-password",
        "recipient_email": None,
    }

    settings_response = client.get("/integrations/smtp/settings", headers=auth_headers["admin"])
    assert settings_response.status_code == 200
    assert settings_response.json()["host"] == "saved.example.com"
    assert settings_response.json()["health_status"] == "unknown"


def test_admin_can_create_multiple_smtp_hooks_and_reuse_credentials(
    client: TestClient,
    auth_headers,
    db_session,
):
    source_response = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Primary relay"),
    )
    assert source_response.status_code == 201
    source = source_response.json()
    assert source["uses_shared_credentials"] is False
    assert source["password_configured"] is True

    shared_payload = _smtp_hook_payload(
        "Alert relay",
        password=None,
        credential_source_id=source["id"],
        event_type="alert_match",
    )
    shared_response = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=shared_payload,
    )
    assert shared_response.status_code == 201
    shared = shared_response.json()
    assert shared["uses_shared_credentials"] is True
    assert shared["credential_source_id"] == source["id"]
    assert shared["credential_source_name"] == "Primary relay"
    assert shared["host"] == "smtp.example.com"
    assert shared["username"] == "relay-user"
    assert shared["password_configured"] is True

    shared_instance = db_session.get(IntegrationInstance, uuid.UUID(shared["id"]))
    assert shared_instance is not None
    assert shared_instance.secret_json is None
    assert shared_instance.config_json["host"] is None
    source_instance = get_smtp_credential_source(db_session, shared_instance)
    active = build_active_smtp_settings(shared_instance, credential_source=source_instance)
    assert active.password == "relay-password"
    assert active.event_types == ["alert_match"]

    hooks_response = client.get("/integrations/smtp/hooks", headers=auth_headers["admin"])
    assert hooks_response.status_code == 200
    assert {hook["name"] for hook in hooks_response.json()} >= {"SMTP", "Primary relay", "Alert relay"}


def test_saved_smtp_hook_test_updates_health_without_resubmitting_secrets(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
):
    hook = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Health relay"),
    ).json()
    captured = {}

    def fake_test(active_settings, *, recipient_email):
        captured["password"] = active_settings.password
        captured["recipient_email"] = recipient_email
        return SMTPTestResponse(
            success=True,
            action="connection",
            duration_ms=9,
            recipient_email=None,
            error_code=None,
            error=None,
            server_message="connected",
            tested_at=datetime.now(timezone.utc),
            used_unsaved_settings=True,
        )

    monkeypatch.setattr("app.api.routes.integrations.test_smtp_integration", fake_test)
    response = client.post(
        "/integrations/smtp/hooks/test",
        headers=auth_headers["admin"],
        json={"hook_id": hook["id"]},
    )
    assert response.status_code == 200
    assert response.json()["used_unsaved_settings"] is False
    assert captured == {"password": "relay-password", "recipient_email": None}

    hooks = client.get("/integrations/smtp/hooks", headers=auth_headers["admin"]).json()
    saved = next(candidate for candidate in hooks if candidate["id"] == hook["id"])
    assert saved["health_status"] == "healthy"
    assert saved["last_test_at"] is not None

    history_response = client.get(
        f"/integrations/smtp/hooks/{hook['id']}/test-runs",
        headers=auth_headers["admin"],
    )
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["total"] == 1
    assert history["page"] == 1
    assert history["page_size"] == 10
    assert history["runs"][0] == {
        "id": history["runs"][0]["id"],
        "hook_id": hook["id"],
        "status": "succeeded",
        "action": "connection",
        "recipient_email": None,
        "used_unsaved_settings": False,
        "duration_ms": 9,
        "error_code": None,
        "error_message": None,
        "server_message": "connected",
        "started_at": history["runs"][0]["started_at"],
        "finished_at": history["runs"][0]["finished_at"],
    }
    test_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "integrations.smtp.hook.test",
            AuditLog.resource_id == hook["id"],
        )
    )
    assert test_audit is not None
    assert test_audit.metadata_json == {
        "run_id": history["runs"][0]["id"],
        "action": "connection",
        "duration_ms": 9,
        "error_code": None,
        "error_message": None,
        "server_message": "connected",
        "recipient_provided": False,
        "used_unsaved_settings": False,
        "used_shared_credentials": False,
    }


def test_unsaved_smtp_hook_test_audit_retains_failure_diagnostics(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
):
    def fake_test(_active_settings, *, recipient_email):
        return SMTPTestResponse(
            success=False,
            action="connection",
            duration_ms=27,
            recipient_email=recipient_email,
            error_code="connection_error",
            error="SMTP connection failed.",
            server_message="Connection refused by relay.",
            tested_at=datetime.now(timezone.utc),
            used_unsaved_settings=False,
        )

    monkeypatch.setattr("app.api.routes.integrations.test_smtp_integration", fake_test)
    draft = _smtp_hook_payload("Unsaved diagnostic relay")
    response = client.post(
        "/integrations/smtp/hooks/test",
        headers=auth_headers["admin"],
        json={"hook": draft},
    )
    assert response.status_code == 200
    assert response.json()["success"] is False

    assert db_session.scalar(
        select(IntegrationInstance).where(IntegrationInstance.name == "Unsaved diagnostic relay")
    ) is None
    test_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "integrations.smtp.hook.test",
            AuditLog.resource_id == "unsaved",
        )
    )
    assert test_audit is not None
    assert test_audit.success is False
    assert test_audit.metadata_json == {
        "run_id": None,
        "action": "connection",
        "duration_ms": 27,
        "error_code": "connection_error",
        "error_message": "SMTP connection failed.",
        "server_message": "Connection refused by relay.",
        "recipient_provided": False,
        "used_unsaved_settings": True,
        "used_shared_credentials": False,
    }


def test_smtp_test_run_history_is_paginated_authorized_and_tolerates_legacy_metadata(
    client: TestClient,
    auth_headers,
    db_session,
):
    hook = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Diagnostic relay"),
    ).json()
    hook_id = uuid.UUID(hook["id"])
    db_session.add_all(
        [
            IntegrationRun(
                integration_id=hook_id,
                run_type="test",
                status="failed",
                started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                duration_ms=102,
                error_code="authentication_error",
                error_message="SMTP authentication failed.",
                metadata_json={
                    "action": "send",
                    "recipient_email": "analyst@example.com",
                    "used_unsaved_settings": True,
                    "server_message": "535 credentials rejected",
                },
            ),
            IntegrationRun(
                integration_id=hook_id,
                run_type="test",
                status="unexpected_legacy_status",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                metadata_json={
                    "action": "unknown",
                    "recipient_email": ["invalid legacy value"],
                    "used_unsaved_settings": "yes",
                    "server_message": 250,
                },
            ),
            IntegrationRun(
                integration_id=hook_id,
                run_type="maintenance",
                status="succeeded",
                started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                metadata_json={},
            ),
        ]
    )
    db_session.commit()

    forbidden = client.get(
        f"/integrations/smtp/hooks/{hook['id']}/test-runs",
        headers=auth_headers["viewer"],
    )
    assert forbidden.status_code == 403

    first_page = client.get(
        f"/integrations/smtp/hooks/{hook['id']}/test-runs?page=1&page_size=1",
        headers=auth_headers["admin"],
    )
    assert first_page.status_code == 200
    payload = first_page.json()
    assert payload["total"] == 2
    assert payload["page_size"] == 1
    assert payload["runs"][0]["status"] == "failed"
    assert payload["runs"][0]["action"] == "send"
    assert payload["runs"][0]["recipient_email"] == "analyst@example.com"
    assert payload["runs"][0]["used_unsaved_settings"] is True
    assert payload["runs"][0]["server_message"] == "535 credentials rejected"

    legacy_page = client.get(
        f"/integrations/smtp/hooks/{hook['id']}/test-runs?page=2&page_size=1",
        headers=auth_headers["admin"],
    )
    assert legacy_page.status_code == 200
    legacy = legacy_page.json()["runs"][0]
    assert legacy["status"] == "failed"
    assert legacy["action"] is None
    assert legacy["recipient_email"] is None
    assert legacy["used_unsaved_settings"] is False
    assert legacy["server_message"] is None


def test_smtp_credential_sources_reject_chains_and_deletion_while_in_use(
    client: TestClient,
    auth_headers,
    db_session,
):
    source = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Reusable relay"),
    ).json()
    dependent = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Dependent relay", password=None, credential_source_id=source["id"]),
    ).json()

    chained = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Chained relay", password=None, credential_source_id=dependent["id"]),
    )
    assert chained.status_code == 409
    assert "already uses shared credentials" in chained.json()["detail"]

    source_delete = client.delete(
        f"/integrations/smtp/hooks/{source['id']}",
        headers=auth_headers["admin"],
    )
    assert source_delete.status_code == 409
    assert "still used" in source_delete.json()["detail"]

    dependent_delete = client.delete(
        f"/integrations/smtp/hooks/{dependent['id']}",
        headers=auth_headers["admin"],
    )
    assert dependent_delete.status_code == 204
    archived = db_session.get(IntegrationInstance, uuid.UUID(dependent["id"]))
    assert archived is not None
    assert archived.enabled is False
    assert archived.secret_json is None
    assert archived.config_json["archived_at"]

    assert client.delete(
        f"/integrations/smtp/hooks/{source['id']}",
        headers=auth_headers["admin"],
    ).status_code == 204


def test_smtp_hooks_reject_unusable_direct_auth_and_protect_credential_dependents(
    client: TestClient,
    auth_headers,
):
    missing_password = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Missing password", password=None),
    )
    assert missing_password.status_code == 422
    assert "password is required" in missing_password.json()["detail"]

    source_payload = _smtp_hook_payload("Protected source")
    source = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=source_payload,
    ).json()
    dependent = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("Protected dependent", password=None, credential_source_id=source["id"]),
    )
    assert dependent.status_code == 201

    source_payload["settings"].update({"enabled": False, "host": None, "password": None})
    remove_host = client.patch(
        f"/integrations/smtp/hooks/{source['id']}",
        headers=auth_headers["admin"],
        json=source_payload,
    )
    assert remove_host.status_code == 409
    assert "must retain an SMTP host" in remove_host.json()["detail"]

    source_payload["settings"].update({"host": "smtp.example.com", "clear_password": True})
    clear_password = client.patch(
        f"/integrations/smtp/hooks/{source['id']}",
        headers=auth_headers["admin"],
        json=source_payload,
    )
    assert clear_password.status_code == 422
    assert "password is required" in clear_password.json()["detail"]


def test_default_smtp_hook_and_template_defaults_remain_backward_compatible(client: TestClient, auth_headers):
    legacy = client.get("/integrations/smtp/settings", headers=auth_headers["admin"])
    assert legacy.status_code == 200
    hook_id = legacy.json()["id"]

    hooks = client.get("/integrations/smtp/hooks", headers=auth_headers["admin"])
    default_hook = next(hook for hook in hooks.json() if hook["id"] == hook_id)
    assert default_hook["is_default"] is True
    assert default_hook["uses_shared_credentials"] is False

    delete_response = client.delete(f"/integrations/smtp/hooks/{hook_id}", headers=auth_headers["admin"])
    assert delete_response.status_code == 409
    assert "default SMTP hook" in delete_response.json()["detail"]

    defaults = client.get("/integrations/smtp/template-defaults", headers=auth_headers["admin"])
    assert defaults.status_code == 200
    assert [entry["send_for"] for entry in defaults.json()] == [
        "rss_item_new",
        "alert_match",
        "feed_failing",
        "webhook_failed",
        "daily_digest",
        "all",
    ]
    assert defaults.json()[1]["event_types"] == ["alert_match"]


def test_smtp_delivery_history_analytics_and_dead_letter_replay(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
):
    hook = client.post(
        "/integrations/smtp/hooks",
        headers=auth_headers["admin"],
        json=_smtp_hook_payload("History relay"),
    ).json()
    hook_id = uuid.UUID(hook["id"])
    delivery = IntegrationDelivery(
        integration_id=hook_id,
        connector_type="smtp",
        event_type="rss_item_new",
        delivery_kind="live",
        state="dead_letter",
        idempotency_key=f"test:{uuid.uuid4()}",
        payload_json={"feed_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4())},
        attempt_count=1,
        max_attempts=1,
        dead_lettered_at=datetime.now(timezone.utc),
        last_duration_ms=14,
        last_error_code="smtp_error",
        last_error_message="Relay rejected the message",
        last_error_retryable=False,
    )
    db_session.add(delivery)
    db_session.flush()
    db_session.add(
        IntegrationAttempt(
            delivery_id=delivery.id,
            integration_id=hook_id,
            attempt_number=1,
            status="failed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_ms=14,
            error_code="smtp_error",
            error_message="Relay rejected the message",
            retryable=False,
            response_json={"recipient_count": 2, "accepted_count": 0},
        )
    )
    db_session.commit()

    history = client.get(
        f"/integrations/smtp/hooks/{hook['id']}/deliveries?page=1&page_size=10",
        headers=auth_headers["admin"],
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["deliveries"][0]["state"] == "dead_letter"
    assert history.json()["deliveries"][0]["attempts"][0]["recipient_count"] == 2

    analytics = client.get("/integrations/smtp/analytics", headers=auth_headers["admin"])
    assert analytics.status_code == 200
    assert analytics.json()["failed_deliveries"] == 1
    assert analytics.json()["failures_last_24h"] == 1
    assert analytics.json()["most_failing_hook"]["hook_name"] == "History relay"

    queued = []
    monkeypatch.setattr(
        "app.api.routes.integrations.enqueue_integration_delivery_processing",
        lambda delivery_ids: queued.extend(delivery_ids) or True,
    )
    replay = client.post(
        f"/integrations/smtp/hooks/{hook['id']}/deliveries/{delivery.id}/replay",
        headers=auth_headers["admin"],
    )
    assert replay.status_code == 200
    assert replay.json()["state"] == "pending"
    assert queued == [uuid.UUID(replay.json()["delivery_id"])]


def test_admin_can_replay_dead_lettered_integration_delivery(
    client: TestClient,
    auth_headers,
    db_session,
    monkeypatch,
):
    instance = IntegrationInstance(
        id=uuid.uuid4(),
        name="Replay SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=True,
        config_json={},
    )
    db_session.add(instance)
    db_session.flush()
    subscription = IntegrationSubscription(
        integration_id=instance.id,
        subscription_key="event:rss_item_new",
        event_type="rss_item_new",
    )
    db_session.add(subscription)
    db_session.flush()
    source = IntegrationDelivery(
        integration_id=instance.id,
        subscription_id=subscription.id,
        connector_type="smtp",
        event_type="rss_item_new",
        state="dead_letter",
        delivery_kind="live",
        idempotency_key=f"dead:{uuid.uuid4()}",
        payload_json={"item_id": str(uuid.uuid4())},
        dead_lettered_at=datetime.now(timezone.utc),
    )
    db_session.add(source)
    db_session.commit()
    queued: list[list[str]] = []
    monkeypatch.setattr(
        "app.tasks.feed_tasks.process_integration_deliveries.delay",
        lambda delivery_ids: queued.append(delivery_ids),
    )

    forbidden = client.post(
        f"/integrations/deliveries/{source.id}/replay",
        headers=auth_headers["viewer"],
    )
    response = client.post(
        f"/integrations/deliveries/{source.id}/replay",
        headers=auth_headers["admin"],
    )

    assert forbidden.status_code == 403
    assert response.status_code == 200
    payload = response.json()
    replay = db_session.get(IntegrationDelivery, uuid.UUID(payload["delivery_id"]))
    assert payload["source_delivery_id"] == str(source.id)
    assert payload["state"] == "pending"
    assert payload["queued"] is True
    assert replay is not None
    assert replay.source_delivery_id == source.id
    assert replay.delivery_kind == "replay"
    assert queued == [[str(replay.id)]]
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "integrations.delivery.replay"))
    assert audit is not None
