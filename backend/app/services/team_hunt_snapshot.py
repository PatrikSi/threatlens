"""Preserve a reviewed hunt in bounded investigation notes without losing fields."""

from app.models.team_item_assessment import TeamItemAssessment
from app.core.api_errors import ApiHTTPException
from app.schemas.team_assessments import HuntSuggestionResponse

NOTE_LIMIT = 10_000
_CONTENT_LIMIT = NOTE_LIMIT - 500
_SNAPSHOT_LIMIT = 32_768


def hunt_snapshot_notes(hunt: dict, assessment: TeamItemAssessment) -> list[str]:
    """Keep quotes and review together first; retain remaining fields in numbered parts.

    All parts are created in the investigation's accepting transaction. Existing
    note permissions and revision history apply to the copied snapshot afterward.
    """
    value = HuntSuggestionResponse.model_validate(hunt)
    sections = [
        f"Analyst-reviewed hunt suggestion: {value.title}",
        f"Hypothesis: {value.hypothesis}",
        *[
            f"Supporting passage ({entry.source}): {entry.quote}"
            for entry in value.evidence
        ],
        f"Analyst review: {value.review_note or 'No additional review note.'}",
        f"Team relevance: {value.rationale}",
    ]
    for label, entries in (
        ("Required telemetry", value.required_logs),
        ("Expected benign explanations", value.benign_explanations),
        ("Information gaps", value.information_gaps),
        ("ATT&CK techniques", value.attack_technique_ids),
        ("ATT&CK detection strategies", value.detection_strategy_ids),
    ):
        sections.extend(f"{label}: {entry}" for entry in entries)
    if sum(len(section) + 2 for section in sections) > _SNAPSHOT_LIMIT:
        raise ApiHTTPException(
            status_code=409,
            error_code="hunt_snapshot_too_large",
            detail="This saved hunt exceeds the handoff limit. Regenerate and review a bounded suggestion before creating an investigation.",
        )
    parts: list[str] = []
    current = ""
    for section in sections:
        # Modern fields fit individually. Split legacy oversized fields without
        # silently dropping text, keeping every stored character recoverable.
        for offset in range(0, len(section), _CONTENT_LIMIT):
            fragment = section[offset:offset + _CONTENT_LIMIT]
            if current and len(current) + 2 + len(fragment) > _CONTENT_LIMIT:
                parts.append(current)
                current = ""
            current = f"{current}\n\n{fragment}" if current else fragment
    if current:
        parts.append(current)
    provenance = (
        f"Assessment {assessment.id}, revision {assessment.version}; "
        f"team context revision {assessment.result_context_version}."
    )
    return [
        f"Hunt snapshot · part {index} of {len(parts)}\n{provenance}\n\n{part}"
        for index, part in enumerate(parts, start=1)
    ]
