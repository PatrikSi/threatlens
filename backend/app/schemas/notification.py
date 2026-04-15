import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

NotificationEventType = Literal["rss_item_new", "alert_match", "feed_failing", "webhook_failed", "daily_digest"]
NotificationMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
NotificationFeedScope = Literal["all", "selected"]
NotificationBodyMode = Literal["none", "json", "form", "raw"]
NotificationDeliveryKind = Literal["live", "retry"]
NotificationDeliveryState = Literal["pending", "sending", "succeeded", "failed"]


class NotificationWebhookField(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(default="", max_length=8000)


class NotificationTemplateVariable(BaseModel):
    key: str
    description: str
    example: str


class NotificationWebhookWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    event_type: NotificationEventType = "rss_item_new"
    url_template: str = Field(min_length=5, max_length=4000)
    method: NotificationMethod = "POST"
    feed_scope: NotificationFeedScope = "all"
    feed_ids: list[uuid.UUID] = Field(default_factory=list)
    query_params: list[NotificationWebhookField] = Field(default_factory=list)
    headers: list[NotificationWebhookField] = Field(default_factory=list)
    body_mode: NotificationBodyMode = "json"
    body_fields: list[NotificationWebhookField] = Field(default_factory=list)
    body_template: str | None = Field(default=None, max_length=20000)
    timeout_seconds: int = Field(default=10, ge=1, le=60)

    @model_validator(mode="after")
    def validate_configuration(self):
        self._extract_query_params_from_url_template()

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

        if self.body_mode == "none":
            self.body_fields = []
            self.body_template = None
        elif self.body_mode == "raw":
            self.body_fields = []
            if not self.body_template or not self.body_template.strip():
                raise ValueError("body_template is required when body_mode is raw")
        else:
            self.body_template = None

        return self

    def _extract_query_params_from_url_template(self):
        try:
            parts = urlsplit(self.url_template)
        except ValueError:
            return

        if not parts.query:
            return

        extracted_fields = [
            NotificationWebhookField(key=key, value=value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key
        ]
        if not extracted_fields:
            self.url_template = urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
            return

        extracted_keys = {field.key for field in extracted_fields}
        retained_fields = [field for field in self.query_params if field.key not in extracted_keys]
        self.query_params = [*retained_fields, *extracted_fields]
        self.url_template = urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


class NotificationWebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    enabled: bool
    event_type: NotificationEventType
    url_template: str
    method: NotificationMethod
    feed_scope: NotificationFeedScope
    feed_ids: list[uuid.UUID]
    query_params: list[NotificationWebhookField]
    headers: list[NotificationWebhookField]
    body_mode: NotificationBodyMode
    body_fields: list[NotificationWebhookField]
    body_template: str | None
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime


class NotificationWebhookTestRequest(BaseModel):
    webhook: NotificationWebhookWrite
    sample_feed_id: uuid.UUID | None = None
    sample_item_id: uuid.UUID | None = None


class NotificationWebhookTestResponse(BaseModel):
    success: bool
    status_code: int | None
    duration_ms: int | None
    rendered_url: str
    rendered_method: NotificationMethod
    rendered_headers: list[NotificationWebhookField]
    rendered_query_params: list[NotificationWebhookField]
    rendered_body: str | None
    response_body_preview: str | None
    error: str | None


class NotificationWebhookDeliveryResponse(BaseModel):
    id: uuid.UUID
    webhook_id: uuid.UUID
    user_id: uuid.UUID
    event_type: NotificationEventType
    item_id: uuid.UUID | None
    feed_id: uuid.UUID | None
    item_title: str | None
    feed_name: str | None
    delivery_kind: NotificationDeliveryKind
    delivery_state: NotificationDeliveryState
    attempt_count: int
    claimed_at: datetime | None
    success: bool
    status_code: int | None
    duration_ms: int | None
    timeout_seconds: int
    rendered_url: str
    rendered_method: NotificationMethod
    rendered_headers: list[NotificationWebhookField]
    rendered_query_params: list[NotificationWebhookField]
    rendered_body: str | None
    response_body_preview: str | None
    error: str | None
    attempted_at: datetime


class NotificationWebhookDeliveryListResponse(BaseModel):
    deliveries: list[NotificationWebhookDeliveryResponse]
    total: int
    page: int
    page_size: int


class NotificationAnalyticsEventSummary(BaseModel):
    event_type: NotificationEventType
    total_deliveries: int
    failed_deliveries: int


class NotificationAnalyticsWebhookSummary(BaseModel):
    webhook_id: uuid.UUID
    webhook_name: str
    failed_deliveries: int
    last_failure_at: datetime | None


class NotificationAnalyticsResponse(BaseModel):
    total_deliveries: int
    successful_deliveries: int
    failed_deliveries: int
    success_rate_pct: float
    failures_last_24h: int
    most_failing_webhook: NotificationAnalyticsWebhookSummary | None
    events: list[NotificationAnalyticsEventSummary]
