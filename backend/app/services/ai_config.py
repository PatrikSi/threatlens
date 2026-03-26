from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_settings import AISettings
from app.schemas.ai import AISettingsResponse, AISettingsUpdate


@dataclass(frozen=True)
class PublicAIFeatureFlags:
    ai_enabled: bool
    ai_configured: bool
    ai_summary_enabled: bool
    ai_relevance_enabled: bool
    ai_daily_brief_enabled: bool


@dataclass(frozen=True)
class ActiveAISettings:
    id: uuid.UUID
    ai_enabled: bool
    ai_configured: bool
    provider_type: str
    base_url: str | None
    model: str | None
    api_key: str | None
    temperature: float
    max_completion_tokens: int
    request_timeout_seconds: int
    summary_enabled: bool
    relevance_enabled: bool
    daily_brief_enabled: bool
    auto_enrich_new_items: bool
    daily_brief_window_hours: int
    daily_brief_max_items: int
    daily_brief_history_limit: int
    relevance_medium_threshold: float
    relevance_high_threshold: float
    company_name: str | None
    company_industry: str | None
    company_regions: list[str]
    company_stack: list[str]
    company_priority_topics: list[str]
    company_keywords: list[str]
    company_exclusions: list[str]
    company_profile_text: str | None
    global_instructions: str | None
    item_summary_instructions: str | None
    relevance_instructions: str | None
    daily_brief_instructions: str | None


def get_or_create_ai_settings(db: Session) -> AISettings:
    settings = db.scalar(select(AISettings).limit(1))
    if settings is not None:
        return settings

    settings = AISettings(
        provider_type="openai_compatible",
        temperature=0.2,
        max_completion_tokens=700,
        request_timeout_seconds=60,
        summary_enabled=True,
        relevance_enabled=True,
        daily_brief_enabled=True,
        auto_enrich_new_items=True,
        daily_brief_window_hours=24,
        daily_brief_max_items=20,
        daily_brief_history_limit=7,
        relevance_medium_threshold=0.55,
        relevance_high_threshold=0.8,
    )
    db.add(settings)
    db.flush()
    return settings


def apply_ai_settings_update(settings: AISettings, payload: AISettingsUpdate) -> None:
    settings.provider_type = payload.provider_type
    settings.base_url = _normalize_optional_text(payload.base_url)
    settings.model = _normalize_optional_text(payload.model)
    settings.temperature = payload.temperature
    settings.max_completion_tokens = payload.max_completion_tokens
    settings.request_timeout_seconds = payload.request_timeout_seconds
    settings.summary_enabled = payload.summary_enabled
    settings.relevance_enabled = payload.relevance_enabled
    settings.daily_brief_enabled = payload.daily_brief_enabled
    settings.auto_enrich_new_items = payload.auto_enrich_new_items
    settings.daily_brief_window_hours = payload.daily_brief_window_hours
    settings.daily_brief_max_items = payload.daily_brief_max_items
    settings.daily_brief_history_limit = payload.daily_brief_history_limit
    settings.relevance_medium_threshold = payload.relevance_medium_threshold
    settings.relevance_high_threshold = payload.relevance_high_threshold
    settings.company_name = _normalize_optional_text(payload.company_name)
    settings.company_industry = _normalize_optional_text(payload.company_industry)
    settings.company_regions_json = list(payload.company_regions)
    settings.company_stack_json = list(payload.company_stack)
    settings.company_priority_topics_json = list(payload.company_priority_topics)
    settings.company_keywords_json = list(payload.company_keywords)
    settings.company_exclusions_json = list(payload.company_exclusions)
    settings.company_profile_text = _normalize_optional_text(payload.company_profile_text)
    settings.global_instructions = _normalize_optional_text(payload.global_instructions)
    settings.item_summary_instructions = _normalize_optional_text(payload.item_summary_instructions)
    settings.relevance_instructions = _normalize_optional_text(payload.relevance_instructions)
    settings.daily_brief_instructions = _normalize_optional_text(payload.daily_brief_instructions)


def ai_settings_response_from_model(settings: AISettings) -> AISettingsResponse:
    runtime_settings = get_settings()
    ai_configured = bool(_normalize_optional_text(settings.base_url) and _normalize_optional_text(settings.model))
    return AISettingsResponse(
        id=settings.id,
        ai_enabled=runtime_settings.ai_enabled,
        ai_configured=ai_configured,
        api_key_configured=bool(runtime_settings.ai_api_key),
        provider_type=settings.provider_type,
        base_url=_normalize_optional_text(settings.base_url),
        model=_normalize_optional_text(settings.model),
        temperature=float(settings.temperature),
        max_completion_tokens=int(settings.max_completion_tokens),
        request_timeout_seconds=int(settings.request_timeout_seconds),
        summary_enabled=bool(settings.summary_enabled),
        relevance_enabled=bool(settings.relevance_enabled),
        daily_brief_enabled=bool(settings.daily_brief_enabled),
        auto_enrich_new_items=bool(settings.auto_enrich_new_items),
        daily_brief_window_hours=int(settings.daily_brief_window_hours),
        daily_brief_max_items=int(settings.daily_brief_max_items),
        daily_brief_history_limit=int(settings.daily_brief_history_limit),
        relevance_medium_threshold=float(settings.relevance_medium_threshold),
        relevance_high_threshold=float(settings.relevance_high_threshold),
        company_name=settings.company_name,
        company_industry=settings.company_industry,
        company_regions=[entry for entry in (settings.company_regions_json or []) if entry],
        company_stack=[entry for entry in (settings.company_stack_json or []) if entry],
        company_priority_topics=[entry for entry in (settings.company_priority_topics_json or []) if entry],
        company_keywords=[entry for entry in (settings.company_keywords_json or []) if entry],
        company_exclusions=[entry for entry in (settings.company_exclusions_json or []) if entry],
        company_profile_text=settings.company_profile_text,
        global_instructions=settings.global_instructions,
        item_summary_instructions=settings.item_summary_instructions,
        relevance_instructions=settings.relevance_instructions,
        daily_brief_instructions=settings.daily_brief_instructions,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def load_public_ai_feature_flags(db: Session) -> PublicAIFeatureFlags:
    runtime_settings = get_settings()
    if not runtime_settings.ai_enabled:
        return PublicAIFeatureFlags(
            ai_enabled=False,
            ai_configured=False,
            ai_summary_enabled=False,
            ai_relevance_enabled=False,
            ai_daily_brief_enabled=False,
        )

    settings = get_or_create_ai_settings(db)
    configured = bool(_normalize_optional_text(settings.base_url) and _normalize_optional_text(settings.model))
    return PublicAIFeatureFlags(
        ai_enabled=True,
        ai_configured=configured,
        ai_summary_enabled=configured and bool(settings.summary_enabled),
        ai_relevance_enabled=configured and bool(settings.relevance_enabled),
        ai_daily_brief_enabled=configured and bool(settings.daily_brief_enabled),
    )


def load_active_ai_settings(db: Session) -> ActiveAISettings:
    runtime_settings = get_settings()
    settings = get_or_create_ai_settings(db)
    base_url = _normalize_optional_text(settings.base_url)
    model = _normalize_optional_text(settings.model)
    configured = bool(runtime_settings.ai_enabled and base_url and model)
    return ActiveAISettings(
        id=settings.id,
        ai_enabled=runtime_settings.ai_enabled,
        ai_configured=configured,
        provider_type=settings.provider_type,
        base_url=base_url,
        model=model,
        api_key=runtime_settings.ai_api_key.strip() if runtime_settings.ai_api_key else None,
        temperature=float(settings.temperature),
        max_completion_tokens=int(settings.max_completion_tokens),
        request_timeout_seconds=int(settings.request_timeout_seconds),
        summary_enabled=bool(settings.summary_enabled),
        relevance_enabled=bool(settings.relevance_enabled),
        daily_brief_enabled=bool(settings.daily_brief_enabled),
        auto_enrich_new_items=bool(settings.auto_enrich_new_items),
        daily_brief_window_hours=int(settings.daily_brief_window_hours),
        daily_brief_max_items=int(settings.daily_brief_max_items),
        daily_brief_history_limit=int(settings.daily_brief_history_limit),
        relevance_medium_threshold=float(settings.relevance_medium_threshold),
        relevance_high_threshold=float(settings.relevance_high_threshold),
        company_name=settings.company_name,
        company_industry=settings.company_industry,
        company_regions=[entry for entry in (settings.company_regions_json or []) if entry],
        company_stack=[entry for entry in (settings.company_stack_json or []) if entry],
        company_priority_topics=[entry for entry in (settings.company_priority_topics_json or []) if entry],
        company_keywords=[entry for entry in (settings.company_keywords_json or []) if entry],
        company_exclusions=[entry for entry in (settings.company_exclusions_json or []) if entry],
        company_profile_text=settings.company_profile_text,
        global_instructions=settings.global_instructions,
        item_summary_instructions=settings.item_summary_instructions,
        relevance_instructions=settings.relevance_instructions,
        daily_brief_instructions=settings.daily_brief_instructions,
    )


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
