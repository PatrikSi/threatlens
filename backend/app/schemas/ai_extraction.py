"""Bounded, source-grounded shared intelligence output contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
EntityID = Annotated[str, StringConstraints(pattern=r"^e[1-9][0-9]{0,2}$")]
EvidenceSource = Literal["title", "summary", "article_text"]
AssertionType = Literal["reported", "inferred"]
EntityKind = Literal["actor", "malware", "product", "behavior", "indicator"]
IndicatorRole = Literal["malicious_infrastructure", "benign", "reference", "unknown"]


class ExtractionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExtractionEvidence(ExtractionContract):
    source: EvidenceSource
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=600)]


class ExtractedEntity(ExtractionContract):
    id: EntityID
    kind: EntityKind
    name: ShortText
    description: Description
    assertion: AssertionType
    evidence: list[ExtractionEvidence] = Field(min_length=1, max_length=3)
    versions: list[ShortText] = Field(default_factory=list, max_length=8)
    indicator_role: IndicatorRole | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "ExtractedEntity":
        if self.versions and self.kind != "product":
            raise ValueError("versions are only valid for products")
        if self.kind == "indicator" and self.indicator_role is None:
            raise ValueError("indicators require a role; use unknown when uncertain")
        if self.kind != "indicator" and self.indicator_role is not None:
            raise ValueError("indicator_role is only valid for indicators")
        return self


class ExtractedRelationship(ExtractionContract):
    source_entity_id: EntityID
    target_entity_id: EntityID
    relationship: ShortText
    description: Description
    assertion: AssertionType
    evidence: list[ExtractionEvidence] = Field(min_length=1, max_length=3)


class StructuredExtraction(ExtractionContract):
    entities: list[ExtractedEntity] = Field(max_length=24)
    relationships: list[ExtractedRelationship] = Field(max_length=24)
    information_gaps: list[ShortText] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_relationships(self) -> "StructuredExtraction":
        entity_ids = {entity.id for entity in self.entities}
        if len(entity_ids) != len(self.entities):
            raise ValueError("entity IDs must be unique")
        for relation in self.relationships:
            if relation.source_entity_id not in entity_ids or relation.target_entity_id not in entity_ids:
                raise ValueError("relationships must reference existing entities")
            if relation.source_entity_id == relation.target_entity_id:
                raise ValueError("relationships must connect distinct entities")
        return self


class VerifiedExtractionEvidence(ExtractionEvidence):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class VerifiedExtractedEntity(ExtractedEntity):
    evidence: list[VerifiedExtractionEvidence] = Field(min_length=1, max_length=3)


class VerifiedExtractedRelationship(ExtractedRelationship):
    evidence: list[VerifiedExtractionEvidence] = Field(min_length=1, max_length=3)


class ExtractionSectionCoverage(BaseModel):
    index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    status: Literal["pending", "started", "completed"]


class ExtractionCoverage(BaseModel):
    planner_version: Literal[1] = 1
    source_hash: str
    normalized_text_chars: int = Field(ge=0)
    processed_chars: int = Field(ge=0)
    uncovered_chars: int = Field(ge=0)
    reserved_tokens: int = Field(ge=0)
    token_budget: int = Field(ge=0)
    call_limit: int = Field(ge=1)
    output_limited: bool = False
    progress_revision: str | None = None
    summary_scope: Literal["first_section", "processed_sections", "section_synthesis"] = "first_section"
    sections: list[ExtractionSectionCoverage] = Field(max_length=32)


class StructuredExtractionResponse(BaseModel):
    schema_version: Literal[1] = 1
    article_id: UUID
    source_version: int
    article_retrieved_at: datetime
    source_hash: str
    input_sha256: str
    truncated: bool
    coverage: ExtractionCoverage | None = None
    entities: list[VerifiedExtractedEntity]
    relationships: list[VerifiedExtractedRelationship]
    information_gaps: list[str]
