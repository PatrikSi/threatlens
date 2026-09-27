"""No credentials or endpoint secrets appear in team governance responses."""

import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator


def provider_key(value: str) -> str:
    if value == "legacy":
        return value
    if value.startswith("profile:"):
        return f"profile:{uuid.UUID(value[8:])}"
    raise ValueError("Choose legacy or profile:<provider UUID>")


class TeamAIGovernancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    approved_provider_keys: list[str] = Field(max_length=100)
    selected_provider_key: str | None = None
    label_destinations: dict[uuid.UUID, list[str]] = Field(
        default_factory=dict, max_length=100
    )

    @field_validator("approved_provider_keys")
    @classmethod
    def keys(cls, values: list[str]) -> list[str]:
        return sorted(set(provider_key(value) for value in values))

    @field_validator("selected_provider_key")
    @classmethod
    def selected(cls, value: str | None) -> str | None:
        return provider_key(value) if value is not None else None

    @field_validator("label_destinations")
    @classmethod
    def labels(cls, values: dict[uuid.UUID, list[str]]) -> dict[uuid.UUID, list[str]]:
        if any(len(keys) > 100 for keys in values.values()):
            raise ValueError(
                "At most 100 destinations per handling label are supported"
            )
        return {
            label: sorted(set(provider_key(value) for value in keys))
            for label, keys in values.items()
        }


class TeamAISelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    selected_provider_key: str | None

    _key = field_validator("selected_provider_key")(
        TeamAIGovernancePolicy.selected.__func__
    )


class TeamAIDestination(BaseModel):
    key: str
    name: str
    available: bool


class TeamAIGovernanceResponse(BaseModel):
    team_id: uuid.UUID
    version: int
    configured: bool
    approved_provider_keys: list[str]
    selected_provider_key: str | None
    label_destinations: dict[str, list[str]]
    destinations: list[TeamAIDestination]
    can_manage: bool = False
    can_approve: bool = False
