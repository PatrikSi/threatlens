from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tagging_rule import TaggingRule
from app.models.tagging_settings import TaggingSettings
from app.schemas.tagging import TaggingRuleResponse, TaggingSettingsResponse, TaggingSettingsUpdate
from app.services.classification import CLASSIFICATION_CATEGORIES


@dataclass(frozen=True)
class ActiveTaggingSettings:
    enabled_categories: set[str]
    min_auto_tag_confidence: float
    secondary_tag_limit: int


def get_or_create_tagging_settings(db: Session) -> TaggingSettings:
    settings = db.scalar(select(TaggingSettings).limit(1))
    if settings is not None:
        return settings

    settings = TaggingSettings(
        enabled_categories_json=list(CLASSIFICATION_CATEGORIES),
        min_auto_tag_confidence=0.45,
        secondary_tag_limit=2,
    )
    db.add(settings)
    db.flush()
    return settings


def tagging_settings_response_from_model(settings: TaggingSettings) -> TaggingSettingsResponse:
    enabled_categories = [category for category in (settings.enabled_categories_json or []) if category in CLASSIFICATION_CATEGORIES]
    if not enabled_categories:
        enabled_categories = list(CLASSIFICATION_CATEGORIES)
    return TaggingSettingsResponse(
        id=settings.id,
        enabled_categories=enabled_categories,
        min_auto_tag_confidence=settings.min_auto_tag_confidence,
        secondary_tag_limit=settings.secondary_tag_limit,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def tagging_rule_response_from_model(rule: TaggingRule) -> TaggingRuleResponse:
    return TaggingRuleResponse(
        id=rule.id,
        name=rule.name,
        tag_name=rule.tag_name,
        enabled=rule.enabled,
        match_type=rule.match_type,
        pattern=rule.pattern,
        case_sensitive=rule.case_sensitive,
        applies_to=list(rule.applies_to_json or []),
        required_categories=list(rule.required_categories_json or []),
        feed_scope=rule.feed_scope,
        feed_ids=[uuid.UUID(value) for value in (rule.feed_ids_json or [])],
        min_classification_confidence=rule.min_classification_confidence,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def apply_tagging_settings_update(settings: TaggingSettings, payload: TaggingSettingsUpdate) -> None:
    settings.enabled_categories_json = list(payload.enabled_categories)
    settings.min_auto_tag_confidence = payload.min_auto_tag_confidence
    settings.secondary_tag_limit = payload.secondary_tag_limit


def load_active_tagging_settings(db: Session) -> ActiveTaggingSettings:
    settings = get_or_create_tagging_settings(db)
    enabled_categories = {
        category
        for category in (settings.enabled_categories_json or [])
        if category in CLASSIFICATION_CATEGORIES
    }
    if not enabled_categories:
        enabled_categories = set(CLASSIFICATION_CATEGORIES)
    return ActiveTaggingSettings(
        enabled_categories=enabled_categories,
        min_auto_tag_confidence=float(settings.min_auto_tag_confidence),
        secondary_tag_limit=max(0, min(2, int(settings.secondary_tag_limit))),
    )


def list_tagging_rules(db: Session) -> list[TaggingRule]:
    return db.scalars(select(TaggingRule).order_by(TaggingRule.created_at.asc())).all()


def list_enabled_tagging_rules(db: Session) -> list[TaggingRule]:
    return db.scalars(
        select(TaggingRule)
        .where(TaggingRule.enabled.is_(True))
        .order_by(TaggingRule.created_at.asc())
    ).all()
