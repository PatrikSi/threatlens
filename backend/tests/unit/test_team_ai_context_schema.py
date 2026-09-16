import pytest
from pydantic import ValidationError

from app.schemas.team_ai_context import TeamAIContextUpdate


def _payload(**changes):
    return {
        "expected_version": 0,
        "technology_stack": ["Windows"],
        "priorities": ["Identity protection"],
        "available_telemetry": ["Authentication logs"],
        "relevance_criteria": "  Prioritize deployed products.  ",
        **changes,
    }


def test_team_ai_context_trims_and_deduplicates_without_mutating_input():
    payload = _payload(technology_stack=[" Windows ", "WINDOWS", "Linux"])
    parsed = TeamAIContextUpdate.model_validate(payload)
    assert parsed.technology_stack == ["Windows", "Linux"]
    assert parsed.relevance_criteria == "Prioritize deployed products."
    assert payload["technology_stack"] == [" Windows ", "WINDOWS", "Linux"]


@pytest.mark.parametrize(
    "changes",
    [
        {"technology_stack": [" "]},
        {"technology_stack": ["x" * 201]},
        {"technology_stack": [str(index) for index in range(41)]},
        {"priorities": ["bad\x00text"]},
        {"available_telemetry": ["bad\ud800text"]},
        {"available_telemetry": "Authentication logs"},
        {"relevance_criteria": "x" * 4001},
        {"relevance_criteria": "bad\x00text"},
        {"expected_version": -1},
        {"team_id": "do-not-accept-a-team-override"},
    ],
)
def test_team_ai_context_rejects_invalid_or_unbounded_values(changes):
    with pytest.raises(ValidationError):
        TeamAIContextUpdate.model_validate(_payload(**changes))


def test_team_ai_context_requires_complete_draft_and_allows_explicit_clearing():
    payload = _payload()
    del payload["priorities"]
    with pytest.raises(ValidationError):
        TeamAIContextUpdate.model_validate(payload)
    cleared = TeamAIContextUpdate.model_validate(
        _payload(
            technology_stack=[],
            priorities=[],
            available_telemetry=[],
            relevance_criteria="",
        )
    )
    assert cleared.relevance_criteria == ""
    assert cleared.technology_stack == []
