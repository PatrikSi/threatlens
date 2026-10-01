"""Bounded, additive contracts for automation subscriptions and credentials."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.outbound_headers import BLOCKED_REQUEST_HEADERS
from app.schemas.storage_text import StorageTextInput, validate_storage_text

ConditionField = Literal[
    "feed_id",
    "tag_id",
    "tag",
    "alert_rule_id",
    "team_id",
    "ioc_type",
    "ioc_role",
    "analyst_verdict",
    "extraction_confidence",
    "maliciousness_confidence",
    "freshness_seconds",
    "attack_technique",
    "hunt_review_status",
    "ai_relevance_score",
    "ai_relevance_label",
]
NUMERIC_FIELDS = frozenset(
    {"extraction_confidence", "maliciousness_confidence", "freshness_seconds", "ai_relevance_score"}
)
INDICATOR_FIELDS = frozenset({
    "ioc_type", "ioc_role", "analyst_verdict",
    "extraction_confidence", "maliciousness_confidence",
})
INDICATOR_OPERATORS = frozenset({"indicators_any", "indicators_all"})


class WebhookCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: ConditionField
    operator: Literal["in", "not_in", "gte", "lte"]
    value: list[str] | float

    @model_validator(mode="after")
    def validate_value(self):
        if self.field in NUMERIC_FIELDS:
            if self.operator not in {"gte", "lte"} or isinstance(self.value, list):
                raise ValueError("Numeric conditions require gte/lte and a number")
            maximum = 31_536_000 if self.field == "freshness_seconds" else 1
            if not 0 <= self.value <= maximum:
                raise ValueError(f"{self.field} must be between 0 and {maximum}")
        elif self.operator not in {"in", "not_in"} or not isinstance(self.value, list):
            raise ValueError("Set conditions require in/not_in and a string array")
        elif not 1 <= len(self.value) <= 50 or any(
            not value or len(value) > 200 for value in self.value
        ):
            raise ValueError(
                "Conditions require 1–50 nonempty values of at most 200 characters"
            )
        if isinstance(self.value, list):
            for value in self.value:
                validate_storage_text(value)
            if self.field == "ai_relevance_label" and any(
                value not in {"low", "medium", "high"} for value in self.value
            ):
                raise ValueError("AI relevance labels must be low, medium, or high")
        return self


class WebhookConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["all", "any", "not", "indicators_any", "indicators_all"]
    conditions: list[WebhookCondition | WebhookConditionGroup] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def validate_budget(self):
        count = 0
        pending = [(self, 1, False)]
        while pending:
            node, depth, indicator_scope = pending.pop()
            count += 1
            if count > 32 or depth > 4:
                raise ValueError(
                    "Condition trees allow at most 32 nodes and four levels"
                )
            if isinstance(node, WebhookConditionGroup):
                if node.op in INDICATOR_OPERATORS:
                    if indicator_scope:
                        raise ValueError("Indicator groups cannot be nested inside indicator groups")
                    indicator_scope = True
                if node.op == "not" and len(node.conditions) != 1:
                    raise ValueError("not requires exactly one condition")
                pending.extend((child, depth + 1, indicator_scope) for child in node.conditions)
            elif indicator_scope and node.field not in INDICATOR_FIELDS:
                raise ValueError("Indicator groups may contain only indicator fields")
        return self


class WebhookCredentialWrite(StorageTextInput):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    auth_type: Literal["none", "bearer", "header"] = "none"
    header_name: str | None = Field(default=None, max_length=128)
    auth_secret: str | None = Field(default=None, max_length=8000)
    clear_auth_secret: bool = False
    signing_secret: str | None = Field(default=None, min_length=32, max_length=4096)
    clear_signing_secret: bool = False
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_secrets(self):
        if not self.name.strip():
            raise ValueError("Credential profile name must not be blank")
        if self.auth_secret is not None and (
            not self.auth_secret
            or self.auth_secret != self.auth_secret.strip()
            or any(not 32 <= ord(char) <= 126 for char in self.auth_secret)
        ):
            raise ValueError(
                "Authentication secrets must be nonempty printable ASCII without control characters or surrounding whitespace"
            )
        if self.clear_auth_secret and self.auth_secret is not None:
            raise ValueError("Choose an authentication secret or clear it")
        if self.clear_signing_secret and self.signing_secret is not None:
            raise ValueError("Choose a signing secret or clear it")
        if self.auth_type == "header":
            if not self.header_name or not re.fullmatch(
                r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self.header_name
            ):
                raise ValueError("Custom authentication requires a valid header name")
            forbidden = BLOCKED_REQUEST_HEADERS | {
                "content-type",
                "cookie",
                "location",
                "authorization",
            }
            if (
                self.header_name.lower() in forbidden
                or self.header_name.lower().startswith("x-threatlens-")
            ):
                raise ValueError("This authentication header is reserved")
        return self


class WebhookCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    enabled: bool
    auth_type: Literal["none", "bearer", "header"]
    header_name: str | None
    auth_configured: bool
    signing_configured: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class WebhookConditionCheck(BaseModel):
    field: str
    matched: bool
    reason: str
    indicator_id: str | None = None
    indicator_type: str | None = None
    indicator_value: str | None = None
    indicator_excluded: bool | None = None
    condition_path: str | None = None


class WebhookPreviewResponse(BaseModel):
    matches: bool
    checks: list[WebhookConditionCheck]
    missing_fields: list[str]
    automation_payload: dict | None = None
