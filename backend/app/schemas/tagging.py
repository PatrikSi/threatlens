import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.classification import CLASSIFICATION_CATEGORIES

TaggingRuleMatchType = Literal["contains", "regex"]
TaggingRuleFeedScope = Literal["all", "selected"]
TaggingRuleField = Literal["title", "summary", "article_text", "feed_name"]


class TaggingSettingsUpdate(BaseModel):
    enabled_categories: list[str] = Field(default_factory=list)
    min_auto_tag_confidence: float = Field(default=0.45, ge=0.05, le=0.995)
    secondary_tag_limit: int = Field(default=2, ge=0, le=2)

    @model_validator(mode="after")
    def validate_enabled_categories(self):
        valid_categories = set(CLASSIFICATION_CATEGORIES)
        deduped: list[str] = []
        seen: set[str] = set()
        for raw_value in self.enabled_categories:
            normalized = (raw_value or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            if normalized not in valid_categories:
                raise ValueError(f"Unknown category: {normalized}")
            seen.add(normalized)
            deduped.append(normalized)
        self.enabled_categories = deduped or list(CLASSIFICATION_CATEGORIES)
        return self


class TaggingSettingsResponse(BaseModel):
    id: uuid.UUID
    enabled_categories: list[str]
    min_auto_tag_confidence: float
    secondary_tag_limit: int
    created_at: datetime
    updated_at: datetime


class TaggingRuleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tag_name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    match_type: TaggingRuleMatchType = "contains"
    pattern: str = Field(min_length=1, max_length=4000)
    case_sensitive: bool = False
    applies_to: list[TaggingRuleField] = Field(default_factory=lambda: ["title", "summary"])
    required_categories: list[str] = Field(default_factory=list)
    feed_scope: TaggingRuleFeedScope = "all"
    feed_ids: list[uuid.UUID] = Field(default_factory=list)
    min_classification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_rule(self):
        self.name = self.name.strip()
        self.pattern = self.pattern.strip()
        normalized_tag_name = self.tag_name.strip().lower()
        if not normalized_tag_name:
            raise ValueError("tag_name cannot be empty")
        self.tag_name = normalized_tag_name

        deduped_applies_to: list[TaggingRuleField] = []
        seen_fields: set[str] = set()
        for field_name in self.applies_to:
            if field_name in seen_fields:
                continue
            seen_fields.add(field_name)
            deduped_applies_to.append(field_name)
        if not deduped_applies_to:
            raise ValueError("applies_to must include at least one field")
        self.applies_to = deduped_applies_to

        valid_categories = set(CLASSIFICATION_CATEGORIES)
        deduped_categories: list[str] = []
        seen_categories: set[str] = set()
        for raw_value in self.required_categories:
            normalized = (raw_value or "").strip().lower()
            if not normalized or normalized in seen_categories:
                continue
            if normalized not in valid_categories:
                raise ValueError(f"Unknown category: {normalized}")
            seen_categories.add(normalized)
            deduped_categories.append(normalized)
        self.required_categories = deduped_categories

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

        return self


class TaggingRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tag_name: str
    enabled: bool
    match_type: TaggingRuleMatchType
    pattern: str
    case_sensitive: bool
    applies_to: list[TaggingRuleField]
    required_categories: list[str]
    feed_scope: TaggingRuleFeedScope
    feed_ids: list[uuid.UUID]
    min_classification_confidence: float | None
    created_at: datetime
    updated_at: datetime


class TaggingSettingsBundleResponse(BaseModel):
    settings: TaggingSettingsResponse
    rules: list[TaggingRuleResponse]


class TaggingRulePreviewItem(BaseModel):
    id: uuid.UUID
    title: str
    feed_name: str
    classification: str | None
    first_seen_at: datetime
    current_tags: list[str]
    matched_sections: list[str]


class TaggingRulePreviewResponse(BaseModel):
    total: int
    items: list[TaggingRulePreviewItem]


class TaggingRulePreviewRequest(TaggingRuleWrite):
    limit: int = Field(default=5, ge=1, le=25)


class TaggingReapplyRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=0, ge=0, le=5000)


class TaggingReapplyResponse(BaseModel):
    task_id: str
    queued: bool = True
