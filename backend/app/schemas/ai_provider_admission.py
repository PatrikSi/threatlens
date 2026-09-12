"""Admission policy is independent of provider wire capabilities."""

from pydantic import BaseModel, Field


class AIProviderAdmissionFields(BaseModel):
    max_concurrent_requests: int = Field(default=0, ge=0, le=1000)
    hourly_token_budget: int = Field(default=0, ge=0, le=1_000_000_000_000)


ADMISSION_FIELDS = tuple(AIProviderAdmissionFields.model_fields)


def admission_limit_values(source: object) -> dict:
    return {
        name: getattr(source, name, field.default)
        for name, field in AIProviderAdmissionFields.model_fields.items()
    }
