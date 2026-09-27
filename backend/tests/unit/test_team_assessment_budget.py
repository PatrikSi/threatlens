"""Keep team prompts within the same budget enforced at the provider boundary."""

import json
import uuid
from types import SimpleNamespace

import pytest

from app.schemas.team_ai_context import TeamAIContextResponse
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_provider_protocol import provider_output_ceiling, validate_provider_request
from app.services.team_assessment_execution import AssessmentSource
from app.services.team_assessment_generation import build_assessment_messages


def _context():
    return TeamAIContextResponse(
        team_id=uuid.uuid4(), version=1, technology_stack=["Windows"],
        priorities=["Endpoint integrity"], available_telemetry=["Process events"],
        relevance_criteria="Protect managed endpoints.",
    )


def _source(*, text="Source reports endpoint behavior. " * 450):
    return AssessmentSource(
        title="Endpoint report", summary="A reported observation.", article_text=text,
        article_id=uuid.uuid4(), article_retrieved_at=None, source_version=1, truncated=False,
    )


def test_small_context_shrinks_only_article_and_reserves_full_output_budget():
    active = SimpleNamespace(model_context_window_tokens=4096, model_max_output_tokens=2048, max_completion_tokens=1024)
    context, source = _context(), _source()
    messages, truncated = build_assessment_messages(active, context=context, source=source, hunts_enabled=True)
    sent = json.loads(messages[1]["content"])
    assert truncated
    assert 0 < len(sent["item"]["article_text"]) < len(source.article_text)
    assert source.article_text.startswith(sent["item"]["article_text"])
    assert sent["team_context"] == context.model_dump(include={
        "technology_stack", "priorities", "available_telemetry", "relevance_criteria",
    })
    assert sent["item"]["title"] == source.title
    assert sent["item"]["summary"] == source.summary
    assert provider_output_ceiling(active, messages) >= active.max_completion_tokens
    validate_provider_request(active, messages, active.max_completion_tokens)


def test_profile_that_cannot_fit_fails_locally_without_silently_changing_team_context():
    active = SimpleNamespace(model_context_window_tokens=4096, model_max_output_tokens=2048, max_completion_tokens=1024)
    context = _context().model_copy(update={"relevance_criteria": "護" * 4000})
    with pytest.raises(AIIntegrationError, match="configured model limits") as caught:
        build_assessment_messages(active, context=context, source=_source(), hunts_enabled=False)
    assert caught.value.provider_io_outcome == "not_sent"


def test_output_limit_failure_is_reported_before_provider_io():
    active = SimpleNamespace(model_context_window_tokens=None, model_max_output_tokens=256, max_completion_tokens=1024)
    with pytest.raises(AIIntegrationError, match="configured model limits") as caught:
        build_assessment_messages(active, context=_context(), source=_source(text="Evidence."), hunts_enabled=False)
    assert caught.value.provider_io_outcome == "not_sent"


def test_context_fitting_metadata_describes_only_complete_passages_actually_sent():
    from datetime import datetime, timezone
    from app.services.ai_extraction import build_verified_extraction
    from app.services.team_evidence_selection import select_assessment_passages
    from tests.unit.test_ai_extraction import ARTICLE, extraction_payload, source_messages

    article_id, retrieved = uuid.uuid4(), datetime.now(timezone.utc)
    text = "Introduction. " * 7000 + ARTICLE
    extraction = build_verified_extraction(extraction_payload(), messages=source_messages(), article_id=article_id,
        article_retrieved_at=retrieved, source_version=1, source_hash="a" * 64, article_text_length=len(text))
    offset = len(text) - len(ARTICLE)
    for entry in [*extraction["entities"], *extraction["relationships"]]:
        for evidence in entry["evidence"]:
            evidence["start"] += offset
            evidence["end"] += offset
    evidence, selection = select_assessment_passages(extraction, source_version=1, article_id=str(article_id),
        retrieved_at=retrieved.isoformat(), context=_context().model_dump(), prefix=text)
    source = _source(text=evidence)
    source.evidence_selection.update(selection)
    assert len(selection["selected_passages"]) > 0
    active = SimpleNamespace(model_context_window_tokens=4096, model_max_output_tokens=2048, max_completion_tokens=1024)
    messages, truncated = build_assessment_messages(active, context=_context(), source=source, hunts_enabled=True)
    sent = json.loads(messages[-1]["content"])
    assert truncated
    assert sent["evidence_selection"]["selected_passages"] == []
    assert sent["evidence_selection"]["selection"] == "article_prefix"
    assert selection["selected_passages"]  # The immutable source snapshot remains unchanged.
    assert source.article_text.startswith(sent["item"]["article_text"])
