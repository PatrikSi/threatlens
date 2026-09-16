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
