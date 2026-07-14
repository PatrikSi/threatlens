from __future__ import annotations

import uuid
from datetime import datetime
from re import split as regex_split
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.notification import NotificationEventType, NotificationFeedScope

IntegrationType = Literal["smtp", "webhook"]
IntegrationDirection = Literal["destination"]
IntegrationHealthStatus = Literal["unknown", "healthy", "warning", "error"]
IntegrationRunStatus = Literal["succeeded", "failed"]
SMTPSecurityMode = Literal["starttls", "ssl_tls", "none"]

DEFAULT_SMTP_EVENT_TYPES: tuple[NotificationEventType, ...] = ("rss_item_new",)
DEFAULT_SMTP_SUBJECT_TEMPLATE = "[ThreatLens] {{ event.type }}: {{ item.title }}"
DEFAULT_SMTP_HTML_TEMPLATE = """<h2>{{ event.type }}</h2>
<p><strong>{{ item.title }}</strong></p>
<p>{{ item.summary }}</p>
<p><a href="{{ item.url }}">Open source item</a></p>
<p>Feed: {{ feed.name }}</p>"""


class IntegrationConnectorResponse(BaseModel):
    integration_type: IntegrationType
    direction: IntegrationDirection
    display_name: str
    description: str
    config_schema_version: int
    supports_test: bool
    capabilities: list[str] = Field(default_factory=list)


class IntegrationSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    integration_type: IntegrationType
    direction: IntegrationDirection
    enabled: bool
    configured: bool
    health_status: IntegrationHealthStatus
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    updated_at: datetime


class IntegrationDeliveryReplayResponse(BaseModel):
    source_delivery_id: uuid.UUID
    delivery_id: uuid.UUID
    state: Literal["pending"]
    queued: bool


class SMTPSettingsUpdate(BaseModel):
    enabled: bool = False
    host: str | None = Field(default=None, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: SMTPSecurityMode = "starttls"
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    clear_password: bool = False
    from_email: EmailStr | None = None
    from_name: str | None = Field(default=None, max_length=255)
    to_emails: list[EmailStr] = Field(default_factory=list, max_length=50)
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    event_types: list[NotificationEventType] = Field(default_factory=lambda: list(DEFAULT_SMTP_EVENT_TYPES))
    feed_scope: NotificationFeedScope = "all"
    feed_ids: list[uuid.UUID] = Field(default_factory=list)
    subject_template: str = Field(default=DEFAULT_SMTP_SUBJECT_TEMPLATE, max_length=500)
    html_template: str = Field(default=DEFAULT_SMTP_HTML_TEMPLATE, max_length=50000)

    @field_validator("host", "username", "password", "from_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None):
        if value is None:
            return value
        if any(char.isspace() for char in value):
            raise ValueError("host must not contain whitespace")
        return value

    @field_validator("to_emails", mode="before")
    @classmethod
    def normalize_to_emails(cls, value):
        if value is None:
            return []
        candidates = regex_split(r"[,;\r\n]+", value) if isinstance(value, str) else value
        if not isinstance(candidates, list):
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            parts = regex_split(r"[,;\r\n]+", candidate) if isinstance(candidate, str) else [candidate]
            for part in parts:
                if part is None:
                    continue
                email = str(part).strip()
                if not email:
                    continue
                dedupe_key = email.lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                normalized.append(email)
        return normalized

    @field_validator("subject_template", "html_template", mode="before")
    @classmethod
    def normalize_required_template(cls, value):
        if value is None:
            return ""
        if not isinstance(value, str):
            return value
        return value.strip()

    @model_validator(mode="after")
    def validate_configuration(self):
        deduped_event_types: list[NotificationEventType] = []
        seen_event_types: set[NotificationEventType] = set()
        for event_type in self.event_types:
            if event_type in seen_event_types:
                continue
            seen_event_types.add(event_type)
            deduped_event_types.append(event_type)
        self.event_types = deduped_event_types

        deduped_feed_ids: list[uuid.UUID] = []
        seen_feed_ids: set[uuid.UUID] = set()
        for feed_id in self.feed_ids:
            if feed_id in seen_feed_ids:
                continue
            seen_feed_ids.add(feed_id)
            deduped_feed_ids.append(feed_id)
        self.feed_ids = deduped_feed_ids

        if self.feed_scope == "all":
            self.feed_ids = []
        elif not self.feed_ids:
            raise ValueError("feed_ids is required when feed_scope is selected")

        if not self.event_types:
            raise ValueError("event_types must contain at least one notification event")
        if not self.subject_template:
            raise ValueError("subject_template is required")
        if not self.html_template:
            raise ValueError("html_template is required")
        if self.password and not self.username:
            raise ValueError("username is required when password is provided")
        if self.clear_password and self.password:
            raise ValueError("clear_password cannot be combined with password")
        if self.enabled:
            if not self.host:
                raise ValueError("host is required when SMTP is enabled")
            if self.from_email is None:
                raise ValueError("from_email is required when SMTP is enabled")
        return self


class SMTPSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    integration_type: IntegrationType
    direction: IntegrationDirection
    enabled: bool
    configured: bool
    schema_version: int
    host: str | None
    port: int
    security: SMTPSecurityMode
    username: str | None
    password_configured: bool
    has_unreadable_secret: bool
    from_email: EmailStr | None
    from_name: str | None
    to_emails: list[EmailStr]
    timeout_seconds: int
    event_types: list[NotificationEventType]
    feed_scope: NotificationFeedScope
    feed_ids: list[uuid.UUID]
    subject_template: str
    html_template: str
    health_status: IntegrationHealthStatus
    last_test_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    last_test_duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class SMTPTestRequest(BaseModel):
    send_email: bool = False
    recipient_email: EmailStr | None = None
    settings: SMTPSettingsUpdate | None = None

    @model_validator(mode="after")
    def validate_send_mode(self):
        if self.recipient_email is not None:
            self.send_email = True
        if self.send_email and self.recipient_email is None:
            raise ValueError("recipient_email is required when send_email is true")
        return self


class SMTPTestResponse(BaseModel):
    success: bool
    action: Literal["connection", "send"]
    duration_ms: int | None
    recipient_email: EmailStr | None
    error_code: str | None
    error: str | None
    server_message: str | None
    tested_at: datetime
    used_unsaved_settings: bool
