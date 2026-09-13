"""Explicit protocol capabilities; no model name implies support for a setting."""

from typing import Literal

from pydantic import BaseModel, Field

from app.core.ai_limits import MAX_AI_COMPLETION_TOKENS


class AIProviderCapabilityFields(BaseModel):
    request_dialect: Literal["chat_completions", "chat_completions_modern"] = "chat_completions"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    structured_output_mode: Literal["off", "json_object"] = "off"
    model_context_window_tokens: int | None = Field(default=None, ge=2048, le=2_097_152)
    model_max_output_tokens: int | None = Field(default=None, ge=128, le=MAX_AI_COMPLETION_TOKENS)


CAPABILITY_FIELDS = tuple(AIProviderCapabilityFields.model_fields)


def capability_values(source: object) -> dict:
    return {
        name: getattr(source, name, field.default)
        for name, field in AIProviderCapabilityFields.model_fields.items()
    }
