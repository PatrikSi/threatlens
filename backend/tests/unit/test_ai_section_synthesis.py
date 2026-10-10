import pytest
from dataclasses import dataclass
from uuid import uuid4

from app.services.ai_extraction_sections import extraction_progress_response
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_section_synthesis import validate_section_synthesis, synthesis_messages, synthesize_sections
from app.services.report_grounding import ReportGroundingError


def test_synthesis_requires_real_section_citations_on_every_claim():
    sections = [{"index": 0, "start": 0, "end": 8000, "extraction": {"entities": [], "relationships": []},
        "completion": {"payload": {"summary_text": "The source describes scheduled task persistence."}}}]
    messages = synthesis_messages(sections)
    validate_section_synthesis({"summary_text": "The source describes scheduled task persistence. [S1]"}, messages)
    with pytest.raises(ReportGroundingError):
        validate_section_synthesis({"summary_text": "The source describes persistence. [S99]"}, messages)
    with pytest.raises(ReportGroundingError):
        validate_section_synthesis({"summary_text": "An unsupported paragraph.\n\nAnother claim. [S1]"}, messages)


@pytest.mark.parametrize("budget,expected_statuses", [
    (128000, ["pending", "failed"]), (10000, ["budget_limited"]),
])
def test_budget_deferral_clears_only_when_synthesis_can_start(budget, expected_statuses):
    @dataclass
    class Settings:
        provider_type: str = "openai_compatible"
        model: str = "fixture"
        summary_enabled: bool = True
        relevance_enabled: bool = False
        structured_extraction_enabled: bool = False
        max_completion_tokens: int = 2048

    completed = [{"index": index, "start": index * 8000, "end": (index + 1) * 8000,
        "status": "completed", "extraction": {"entities": [], "relationships": []},
        "completion": {"payload": {"summary_text": "Synthetic source evidence."}}}
        for index in range(2)]
    progress = {"source_hash": "a" * 64, "text_chars": 16000,
        "reserved_tokens": 10000, "token_budget": budget, "sections": completed,
        "synthesis_deferred": "total_token_budget"}
    saved_statuses = []
    requests = []

    def save():
        saved_statuses.append(extraction_progress_response(progress).synthesis_status)

    def request(*args, **kwargs):
        requests.append(kwargs)
        raise AIIntegrationError("Safe provider failure", provider_io_outcome="not_sent")

    assert synthesize_sections(None, Settings(), progress=progress, completed=completed,
        item_id=uuid4(), task_run_id=uuid4(), checkpoint=lambda: None, save=save, request=request) is None
    assert saved_statuses == expected_statuses
    assert extraction_progress_response(progress).synthesis_status == expected_statuses[-1]
    if budget == 128000:
        assert len(requests) == 1
        assert progress["synthesis"]["status"] == "failed"
        assert "synthesis_deferred" not in progress
    else:
        assert requests == []
        assert progress["synthesis_deferred"] == "total_token_budget"
