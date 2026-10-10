"""Explicit result contracts shared by AI workers, routes and tests."""
import uuid
from dataclasses import dataclass
from app.models.ai_daily_brief import AIDailyBrief
from app.models.item_ai_enrichment import ItemAIEnrichment


@dataclass(frozen=True)
class AIItemEnrichmentResult:
    enrichment: ItemAIEnrichment | None
    status: str
    reason: str | None
    input_text_chars: int
    prompt_char_count: int | None = None
    response_char_count: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class AIDailyBriefGenerationResult:
    brief: AIDailyBrief | None
    status: str
    reason: str | None
    items_considered: int
    items_selected: int
    prompt_char_count: int | None = None
    response_char_count: int | None = None
    integration_event_id: uuid.UUID | None = None
    error: str | None = None
