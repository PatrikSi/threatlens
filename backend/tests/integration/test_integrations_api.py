import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.integration import IntegrationInstance
from app.schemas.integration import SMTPTestResponse
from app.services.secret_storage import is_encrypted_json


def test_admin_can_manage_smtp_settings_without_secret_leakage(client: TestClient, auth_headers, db_session):
    connectors_response = client.get("/integrations/connectors", headers=auth_headers["admin"])
    assert connectors_response.status_code == 200
    assert connectors_response.json()[0]["integration_type"] == "smtp"

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
