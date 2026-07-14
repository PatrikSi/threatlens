import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.models.integration import IntegrationInstance
from app.schemas.integration import SMTPSettingsUpdate, SMTPTestResponse
from app.services.integration_storage import (
    apply_smtp_settings_update,
    build_active_smtp_settings,
    read_smtp_secret_config,
    record_smtp_test_result,
    smtp_settings_response_from_model,
)
from app.services.secret_storage import is_encrypted_json


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_smtp_settings_store_password_as_write_only_encrypted_secret():
    instance = _smtp_instance()
    payload = SMTPSettingsUpdate(
        enabled=True,
        host="smtp.example.com",
        port=587,
        security="starttls",
        username="relay-user",
        password="relay-password",
        from_email="threatlens@example.com",
        to_emails=["analyst@example.com", "soc@example.com"],
        timeout_seconds=10,
    )

    apply_smtp_settings_update(instance, payload)
    response = smtp_settings_response_from_model(instance)
    active = build_active_smtp_settings(instance)

    assert is_encrypted_json(instance.secret_json)
    assert response.password_configured is True
    assert response.has_unreadable_secret is False
    assert response.to_emails == ["analyst@example.com", "soc@example.com"]
    assert active.password == "relay-password"
    assert active.to_emails == ["analyst@example.com", "soc@example.com"]


def test_smtp_secret_can_be_replaced_or_cleared_without_leaking_previous_value():
    instance = _smtp_instance()
    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            host="smtp.example.com",
            username="relay-user",
            password="old-password",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )

    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            host="smtp.example.com",
            username="relay-user",
            password="new-password",
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    secrets, error = read_smtp_secret_config(instance)
    assert error is None
    assert secrets == {"password": "new-password"}

    apply_smtp_settings_update(
        instance,
        SMTPSettingsUpdate(
            host="smtp.example.com",
            username="relay-user",
            clear_password=True,
            from_email="threatlens@example.com",
            to_emails=["analyst@example.com"],
        ),
    )
    secrets, error = read_smtp_secret_config(instance)
    assert error is None
    assert secrets == {}


def test_smtp_test_result_updates_saved_health_only_for_saved_settings(db_session):
    instance = _smtp_instance()
    db_session.add(instance)
    db_session.flush()
    result = SMTPTestResponse(
        success=True,
        action="connection",
        duration_ms=25,
        recipient_email=None,
        error_code=None,
        error=None,
        server_message="250 OK",
        tested_at=datetime.now(timezone.utc),
        used_unsaved_settings=False,
    )

    record_smtp_test_result(db_session, instance=instance, result=result, used_unsaved_settings=False)

    assert instance.health_status == "healthy"
    assert instance.last_success_at == result.tested_at
    assert instance.last_error is None


def _smtp_instance() -> IntegrationInstance:
    now = datetime.now(timezone.utc)
    return IntegrationInstance(
        id=uuid.uuid4(),
        name="SMTP",
        integration_type="smtp",
        direction="destination",
        enabled=False,
        schema_version=1,
        config_json={},
        secret_json=None,
        health_status="unknown",
        created_at=now,
        updated_at=now,
    )
