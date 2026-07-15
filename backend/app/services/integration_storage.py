from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import get_args

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.integration import (
    IntegrationInstance,
    IntegrationRun,
    IntegrationSubscription,
    IntegrationSubscriptionFeed,
)
from app.schemas.integration import (
    DEFAULT_SMTP_EVENT_TYPES,
    DEFAULT_SMTP_HTML_TEMPLATE,
    DEFAULT_SMTP_SUBJECT_TEMPLATE,
    IntegrationSummaryResponse,
    SMTPSettingsResponse,
    SMTPSettingsUpdate,
    SMTPTestResponse,
)
from app.schemas.notification import NotificationEventType
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION
from app.services.secret_storage import decrypt_json, encrypt_json

SMTP_SYSTEM_KEY = "smtp.default"
SMTP_INTEGRATION_TYPE = "smtp"
INTEGRATION_DIRECTION_DESTINATION = "destination"
INTEGRATION_HEALTH_UNKNOWN = "unknown"
INTEGRATION_HEALTH_HEALTHY = "healthy"
INTEGRATION_HEALTH_ERROR = "error"
VALID_SMTP_EVENT_TYPES = frozenset(get_args(NotificationEventType))
EMAIL_ADAPTER = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class ActiveSMTPSettings:
    id: uuid.UUID
    enabled: bool
    host: str | None
    port: int
    security: str
    username: str | None
    password: str | None
    from_email: str | None
    from_name: str | None
    to_emails: list[str]
    timeout_seconds: int
    event_types: list[str]
    feed_scope: str
    feed_ids: list[uuid.UUID]
    subject_template: str
    html_template: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_email and self.to_emails)


class SMTPSecretError(ValueError):
    pass


def get_or_create_smtp_integration(db: Session) -> IntegrationInstance:
    instance = db.scalar(select(IntegrationInstance).where(IntegrationInstance.system_key == SMTP_SYSTEM_KEY))
    if instance is not None:
        return instance

    instance = IntegrationInstance(
        system_key=SMTP_SYSTEM_KEY,
        name="SMTP",
        integration_type=SMTP_INTEGRATION_TYPE,
        direction=INTEGRATION_DIRECTION_DESTINATION,
        enabled=False,
        schema_version=SMTP_CONFIG_SCHEMA_VERSION,
        config_json=_default_smtp_config(),
        secret_json=None,
        health_status=INTEGRATION_HEALTH_UNKNOWN,
    )
    try:
        with db.begin_nested():
            db.add(instance)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(IntegrationInstance).where(IntegrationInstance.system_key == SMTP_SYSTEM_KEY))
        if existing is None:
            raise
        return existing
    return instance


def get_or_create_persisted_smtp_integration(db: Session) -> IntegrationInstance:
    instance = db.scalar(select(IntegrationInstance).where(IntegrationInstance.system_key == SMTP_SYSTEM_KEY))
    if instance is not None:
        return instance
    instance = get_or_create_smtp_integration(db)
    db.commit()
    db.refresh(instance)
    return instance


def lock_smtp_configuration(db: Session) -> IntegrationInstance:
    instance = get_or_create_smtp_integration(db)
    db.flush()
    locked = db.scalar(
        select(IntegrationInstance)
        .where(IntegrationInstance.system_key == SMTP_SYSTEM_KEY)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return locked or instance


def list_integration_summaries(db: Session) -> list[IntegrationSummaryResponse]:
    instances = db.scalars(select(IntegrationInstance).order_by(IntegrationInstance.name.asc())).all()
    summaries: list[IntegrationSummaryResponse] = []
    for instance in instances:
        if instance.integration_type == SMTP_INTEGRATION_TYPE and not smtp_instance_is_archived(instance):
            try:
                credential_source = get_smtp_credential_source(db, instance)
                smtp = smtp_settings_response_from_model(instance, credential_source=credential_source)
            except SMTPSecretError as exc:
                smtp = smtp_settings_response_from_model(instance).model_copy(
                    update={
                        "configured": False,
                        "health_status": INTEGRATION_HEALTH_ERROR,
                        "last_error": str(exc),
                    }
                )
            summaries.append(
                IntegrationSummaryResponse(
                    id=instance.id,
                    name=instance.name,
                    integration_type="smtp",
                    direction="destination",
                    enabled=smtp.enabled,
                    configured=smtp.configured,
                    health_status=smtp.health_status,
                    last_success_at=smtp.last_success_at,
                    last_error_at=smtp.last_error_at,
                    last_error=smtp.last_error,
                    updated_at=smtp.updated_at,
                )
            )
    return summaries


def apply_smtp_settings_update(instance: IntegrationInstance, payload: SMTPSettingsUpdate) -> None:
    instance.integration_type = SMTP_INTEGRATION_TYPE
    instance.direction = INTEGRATION_DIRECTION_DESTINATION
    instance.enabled = payload.enabled
    instance.schema_version = SMTP_CONFIG_SCHEMA_VERSION
    instance.config_json = _smtp_config_from_payload(payload)

    if payload.password is not None:
        instance.secret_json = encrypt_json({"password": payload.password})
    elif payload.clear_password:
        instance.secret_json = None

    instance.health_status = INTEGRATION_HEALTH_UNKNOWN
    instance.last_test_at = None
    instance.last_success_at = None
    instance.last_error_at = None
    instance.last_error = None
    instance.last_test_duration_ms = None


def apply_smtp_hook_settings_update(
    instance: IntegrationInstance,
    payload: SMTPSettingsUpdate,
    *,
    name: str,
    credential_source: IntegrationInstance | None,
) -> None:
    apply_smtp_settings_update(instance, payload)
    instance.name = name
    instance.credential_source_integration_id = credential_source.id if credential_source is not None else None
    if credential_source is not None:
        config = dict(instance.config_json)
        config["host"] = None
        config["username"] = None
        instance.config_json = config
        instance.secret_json = None


def sync_smtp_subscriptions(db: Session, instance: IntegrationInstance) -> list[IntegrationSubscription]:
    """Synchronize SMTP routing rows while retaining disabled rows for delivery history."""
    config = _normalize_smtp_config(instance.config_json)
    configured_event_types = set(config["event_types"])
    subscriptions = {
        subscription.event_type: subscription
        for subscription in db.scalars(
            select(IntegrationSubscription).where(IntegrationSubscription.integration_id == instance.id)
        ).all()
    }
    active_subscriptions: list[IntegrationSubscription] = []
    for event_type in sorted(configured_event_types):
        subscription = subscriptions.get(event_type)
        if subscription is None:
            subscription = IntegrationSubscription(
                integration_id=instance.id,
                subscription_key=f"event:{event_type}",
                event_type=event_type,
            )
            db.add(subscription)
        subscription.subscription_key = f"event:{event_type}"
        subscription.enabled = bool(instance.enabled)
        subscription.feed_scope = config["feed_scope"]
        subscription.filter_json = {
            "feed_scope": config["feed_scope"],
            "feed_ids": [str(feed_id) for feed_id in config["feed_ids"]],
        }
        subscription.transform_json = {}
        db.flush()
        _sync_smtp_subscription_feeds(db, subscription=subscription, feed_ids=set(config["feed_ids"]))
        active_subscriptions.append(subscription)

    for event_type, subscription in subscriptions.items():
        if event_type not in configured_event_types:
            subscription.enabled = False
            db.add(subscription)
    db.flush()
    return active_subscriptions


def smtp_settings_response_from_model(
    instance: IntegrationInstance,
    *,
    credential_source: IntegrationInstance | None = None,
) -> SMTPSettingsResponse:
    config = _normalize_smtp_config(instance.config_json)
    credential_instance = credential_source or instance
    credential_config = _normalize_smtp_config(credential_instance.config_json)
    secrets, secret_error = read_smtp_secret_config(credential_instance)
    health_status = instance.health_status
    last_error = instance.last_error
    if secret_error:
        health_status = INTEGRATION_HEALTH_ERROR
        last_error = secret_error

    return SMTPSettingsResponse(
        id=instance.id,
        name=instance.name,
        integration_type="smtp",
        direction="destination",
        enabled=bool(instance.enabled),
        configured=_smtp_configured(
            config,
            credential_config=credential_config,
            password_configured=bool(secrets.get("password")),
        ),
        schema_version=int(instance.schema_version or SMTP_CONFIG_SCHEMA_VERSION),
        host=credential_config["host"],
        port=credential_config["port"],
        security=credential_config["security"],
        username=credential_config["username"],
        password_configured=bool(secrets.get("password")) if not secret_error else False,
        has_unreadable_secret=bool(secret_error),
        from_email=config["from_email"],
        from_name=config["from_name"],
        to_emails=config["to_emails"],
        timeout_seconds=config["timeout_seconds"],
        event_types=config["event_types"],
        feed_scope=config["feed_scope"],
        feed_ids=config["feed_ids"],
        subject_template=config["subject_template"],
        html_template=config["html_template"],
        health_status=health_status,
        last_test_at=instance.last_test_at,
        last_success_at=instance.last_success_at,
        last_error_at=instance.last_error_at,
        last_error=last_error,
        last_test_duration_ms=instance.last_test_duration_ms,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


def build_active_smtp_settings(
    instance: IntegrationInstance,
    *,
    override: SMTPSettingsUpdate | None = None,
    credential_source: IntegrationInstance | None = None,
) -> ActiveSMTPSettings:
    credential_instance = credential_source or instance
    credential_config = _normalize_smtp_config(credential_instance.config_json)
    if override is not None:
        config = _smtp_config_from_payload(override)
        if credential_source is None:
            credential_config = config
            password = _resolve_override_password(instance, override)
        else:
            secrets, secret_error = read_smtp_secret_config(credential_source)
            if secret_error:
                raise SMTPSecretError(secret_error)
            password = secrets.get("password")
        return ActiveSMTPSettings(
            id=instance.id,
            enabled=override.enabled,
            host=credential_config["host"],
            port=credential_config["port"],
            security=credential_config["security"],
            username=credential_config["username"],
            password=password,
            from_email=config["from_email"],
            from_name=config["from_name"],
            to_emails=config["to_emails"],
            timeout_seconds=config["timeout_seconds"],
            event_types=config["event_types"],
            feed_scope=config["feed_scope"],
            feed_ids=config["feed_ids"],
            subject_template=config["subject_template"],
            html_template=config["html_template"],
        )

    config = _normalize_smtp_config(instance.config_json)
    secrets, secret_error = read_smtp_secret_config(credential_instance)
    if secret_error:
        raise SMTPSecretError(secret_error)
    return ActiveSMTPSettings(
        id=instance.id,
        enabled=bool(instance.enabled),
        host=credential_config["host"],
        port=credential_config["port"],
        security=credential_config["security"],
        username=credential_config["username"],
        password=secrets.get("password"),
        from_email=config["from_email"],
        from_name=config["from_name"],
        to_emails=config["to_emails"],
        timeout_seconds=config["timeout_seconds"],
        event_types=config["event_types"],
        feed_scope=config["feed_scope"],
        feed_ids=config["feed_ids"],
        subject_template=config["subject_template"],
        html_template=config["html_template"],
    )


def read_smtp_secret_config(instance: IntegrationInstance) -> tuple[dict[str, str], str | None]:
    if not instance.secret_json:
        return {}, None
    try:
        decrypted = decrypt_json(instance.secret_json) or {}
    except ValueError:
        return {}, "Stored SMTP secret cannot be decrypted. Enter a new password or clear the saved password."
    if not isinstance(decrypted, dict):
        return {}, "Stored SMTP secret has an invalid format. Enter a new password or clear the saved password."

    password = decrypted.get("password")
    if password is None:
        return {}, None
    if not isinstance(password, str):
        return {}, "Stored SMTP password has an invalid format. Enter a new password or clear the saved password."
    return {"password": password}, None


def get_smtp_credential_source(db: Session, instance: IntegrationInstance) -> IntegrationInstance | None:
    source_id = instance.credential_source_integration_id
    if source_id is None:
        return None
    source = db.get(IntegrationInstance, source_id)
    if source is None or source.integration_type != SMTP_INTEGRATION_TYPE or smtp_instance_is_archived(source):
        raise SMTPSecretError("The shared SMTP credential source is no longer available. Choose another source or enter new credentials.")
    if source.credential_source_integration_id is not None:
        raise SMTPSecretError("The shared SMTP credential source is invalid because credential chains are not supported.")
    return source


def smtp_instance_is_archived(instance: IntegrationInstance) -> bool:
    config = instance.config_json if isinstance(instance.config_json, dict) else {}
    return bool(config.get("archived_at"))


def record_smtp_test_result(
    db: Session,
    *,
    instance: IntegrationInstance,
    result: SMTPTestResponse,
    used_unsaved_settings: bool,
) -> IntegrationRun:
    run = IntegrationRun(
        integration_id=instance.id,
        run_type="test",
        status="succeeded" if result.success else "failed",
        started_at=result.tested_at,
        finished_at=result.tested_at,
        duration_ms=result.duration_ms,
        error_code=result.error_code,
        error_message=result.error,
        metadata_json={
            "action": result.action,
            "recipient_email": str(result.recipient_email) if result.recipient_email else None,
            "used_unsaved_settings": used_unsaved_settings,
        },
    )
    db.add(run)

    if not used_unsaved_settings:
        instance.last_test_at = result.tested_at
        instance.last_test_duration_ms = result.duration_ms
        if result.success:
            instance.health_status = INTEGRATION_HEALTH_HEALTHY
            instance.last_success_at = result.tested_at
            instance.last_error = None
        else:
            instance.health_status = INTEGRATION_HEALTH_ERROR
            instance.last_error_at = result.tested_at
            instance.last_error = result.error
        db.add(instance)

    return run


def _resolve_override_password(instance: IntegrationInstance, payload: SMTPSettingsUpdate) -> str | None:
    if payload.password is not None:
        return payload.password
    if payload.clear_password:
        return None
    secrets, secret_error = read_smtp_secret_config(instance)
    if secret_error:
        raise SMTPSecretError(secret_error)
    return secrets.get("password")


def _smtp_config_from_payload(payload: SMTPSettingsUpdate) -> dict:
    return {
        "host": _normalize_optional_text(payload.host),
        "port": int(payload.port),
        "security": payload.security,
        "username": _normalize_optional_text(payload.username),
        "from_email": str(payload.from_email) if payload.from_email is not None else None,
        "from_name": _normalize_optional_text(payload.from_name),
        "to_emails": [str(email) for email in payload.to_emails],
        "timeout_seconds": int(payload.timeout_seconds),
        "event_types": list(payload.event_types),
        "feed_scope": payload.feed_scope,
        "feed_ids": [str(feed_id) for feed_id in payload.feed_ids],
        "subject_template": payload.subject_template.strip(),
        "html_template": payload.html_template.strip(),
    }


def _normalize_smtp_config(value) -> dict:
    config = value if isinstance(value, dict) else {}
    defaults = _default_smtp_config()
    normalized = {
        "host": _normalize_optional_text(config.get("host")),
        "port": _coerce_int(config.get("port"), default=defaults["port"], minimum=1, maximum=65535),
        "security": _normalize_security(config.get("security")),
        "username": _normalize_optional_text(config.get("username")),
        "from_email": _normalize_optional_text(config.get("from_email")),
        "from_name": _normalize_optional_text(config.get("from_name")),
        "to_emails": _normalize_email_list(config.get("to_emails")),
        "timeout_seconds": _coerce_int(
            config.get("timeout_seconds"),
            default=defaults["timeout_seconds"],
            minimum=1,
            maximum=60,
        ),
        "event_types": _normalize_event_types(config.get("event_types"), default=defaults["event_types"]),
        "feed_scope": _normalize_feed_scope(config.get("feed_scope")),
        "feed_ids": _normalize_feed_ids(config.get("feed_ids")),
        "subject_template": _normalize_required_text(
            config.get("subject_template"),
            default=defaults["subject_template"],
        ),
        "html_template": _normalize_required_text(config.get("html_template"), default=defaults["html_template"]),
    }
    if normalized["feed_scope"] == "all":
        normalized["feed_ids"] = []
    return normalized


def _default_smtp_config() -> dict:
    return {
        "host": None,
        "port": 587,
        "security": "starttls",
        "username": None,
        "from_email": None,
        "from_name": None,
        "to_emails": [],
        "timeout_seconds": 10,
        "event_types": list(DEFAULT_SMTP_EVENT_TYPES),
        "feed_scope": "all",
        "feed_ids": [],
        "subject_template": DEFAULT_SMTP_SUBJECT_TEMPLATE,
        "html_template": DEFAULT_SMTP_HTML_TEMPLATE,
    }


def _smtp_configured(
    config: dict,
    *,
    credential_config: dict | None = None,
    password_configured: bool = False,
) -> bool:
    credentials = credential_config or config
    authentication_configured = not credentials.get("username") or password_configured
    return bool(
        credentials.get("host")
        and authentication_configured
        and config.get("from_email")
        and config.get("to_emails")
    )


def _sync_smtp_subscription_feeds(
    db: Session,
    *,
    subscription: IntegrationSubscription,
    feed_ids: set[uuid.UUID],
) -> None:
    valid_feed_ids = (
        set(db.scalars(select(Feed.id).where(Feed.id.in_(feed_ids))).all())
        if feed_ids
        else set()
    )
    existing = {
        row.feed_id: row
        for row in db.scalars(
            select(IntegrationSubscriptionFeed).where(
                IntegrationSubscriptionFeed.subscription_id == subscription.id
            )
        ).all()
    }
    for feed_id, row in existing.items():
        if feed_id not in valid_feed_ids:
            db.delete(row)
    for feed_id in valid_feed_ids - set(existing):
        db.add(IntegrationSubscriptionFeed(subscription_id=subscription.id, feed_id=feed_id))


def _normalize_optional_text(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    return normalized or None


def _normalize_security(value) -> str:
    normalized = _normalize_optional_text(value) or "starttls"
    if normalized not in {"starttls", "ssl_tls", "none"}:
        return "starttls"
    return normalized


def _normalize_event_types(value, *, default: list[str]) -> list[str]:
    candidates = value if isinstance(value, list) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or candidate not in VALID_SMTP_EVENT_TYPES or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized or list(default)


def _normalize_feed_scope(value) -> str:
    normalized = _normalize_optional_text(value) or "all"
    if normalized not in {"all", "selected"}:
        return "all"
    return normalized


def _normalize_feed_ids(value) -> list[uuid.UUID]:
    candidates = value if isinstance(value, list) else []
    normalized: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for candidate in candidates:
        try:
            feed_id = candidate if isinstance(candidate, uuid.UUID) else uuid.UUID(str(candidate))
        except (TypeError, ValueError):
            continue
        if feed_id in seen:
            continue
        seen.add(feed_id)
        normalized.append(feed_id)
    return normalized


def _normalize_email_list(value) -> list[str]:
    candidates = value if isinstance(value, list) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        email = _normalize_optional_text(candidate)
        if email is None:
            continue
        try:
            email = str(EMAIL_ADAPTER.validate_python(email))
        except ValueError:
            continue
        dedupe_key = email.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(email)
    return normalized


def _normalize_required_text(value, *, default: str) -> str:
    normalized = _normalize_optional_text(value)
    return normalized if normalized is not None else default


def _coerce_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(candidate, minimum), maximum)
