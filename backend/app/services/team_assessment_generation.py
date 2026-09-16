"""Generate team-specific interpretation without changing shared article facts."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.schemas.team_ai_context import TeamAIContextResponse
from app.services.ai_config import ActiveAISettings, load_active_ai_settings
from app.services.ai_context_budget import estimate_tokens
from app.services.ai_integration import request_ai_json_with_usage
from app.services.ai_ops import finish_ai_task_run
from app.services.ai_provider_client import AIIntegrationError, AICompletionResult
from app.services.ai_provider_protocol import provider_output_ceiling, validate_provider_request
from app.services.attack_catalog import detection_strategies_for_techniques
from app.services.team_assessment_contract import ASSESSMENT_SYSTEM_PROMPT, validate_team_assessment_output
from app.services.team_assessment_execution import FEATURE, AssessmentSource, fence_team_assessment


def build_assessment_messages(
    active: ActiveAISettings, *, context: TeamAIContextResponse,
    source: AssessmentSource, hunts_enabled: bool,
) -> tuple[list[dict[str, str]], bool]:
    team = context.model_dump(include={"technology_stack", "priorities", "available_telemetry", "relevance_criteria"})
    item = {"title": source.title, "summary": source.summary, "article_text": source.article_text}
    payload = {"task": FEATURE, "team_context": team, "item": item, "hunts_enabled": hunts_enabled}
    messages = [
        {"role": "system", "content": ASSESSMENT_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    truncated = source.truncated
    context_limit = active.model_context_window_tokens
    if context_limit is not None:
        while active.max_completion_tokens > provider_output_ceiling(active, messages) and item["article_text"]:
            # Shrink only evidence, preserving the complete bounded team profile.
            item["article_text"] = item["article_text"][:len(item["article_text"]) * 3 // 4]
            messages[1]["content"] = json.dumps(payload, ensure_ascii=False)
            truncated = True
    validate_provider_request(active, messages, active.max_completion_tokens)
    if not any(item.values()):
        raise AIIntegrationError("This article has no usable evidence for assessment.", retryable=False, provider_io_outcome="not_sent")
    return messages, truncated


def _completion_retry_limit(active: ActiveAISettings, messages: list[dict[str, str]]) -> int:
    return provider_output_ceiling(active, messages)


def _reviewable_result(completion: AICompletionResult, messages: list[dict[str, str]], *, truncated: bool) -> dict:
    result = validate_team_assessment_output(completion.payload, messages)
    if truncated:
        result["information_gaps"] = [
            "Assessment uses a bounded source excerpt; omitted article text may change its conclusions.",
            *result["information_gaps"][:7],
        ]
    for hunt in result["hunts"]:
        if not hunt["detection_strategy_ids"]:
            hunt["detection_strategy_ids"] = [
                entry["id"] for entry in detection_strategies_for_techniques(hunt["attack_technique_ids"], limit=5)
            ]
        hunt.update(id=str(uuid.uuid4()), review_status="suggested", review_note=None, investigation_id=None)
    return result


def generate_team_assessment(db: Session, *, run_id: uuid.UUID) -> AICompletionResult:
    active = load_active_ai_settings(db, feature_type=FEATURE, task_run_id=run_id)
    if not active.ai_enabled or not active.ai_configured:
        raise AIIntegrationError(
            active.configuration_error or "AI generation is disabled or its provider is not configured. Review AI settings and generate again.",
            retryable=False, provider_io_outcome="not_sent",
        )
    work, context, source, hunts_enabled = fence_team_assessment(db, run_id=run_id)
    messages, truncated = build_assessment_messages(active, context=context, source=source, hunts_enabled=hunts_enabled)
    item_id = work.item_id
    db.commit()

    def checkpoint() -> None:
        fence_team_assessment(db, run_id=run_id)

    completion = request_ai_json_with_usage(
        db, active, feature_type=FEATURE, messages=messages, item_id=item_id, task_run_id=run_id,
        provider_operation_scope=FEATURE, execution_checkpoint=checkpoint,
        request_authorization_check=checkpoint,
        max_retry_completion_tokens=_completion_retry_limit(active, messages),
    )
    # Provider receipt commits do not grant permission to publish. Reacquire all
    # fences, including the logical delivery, before changing the canonical row.
    work, _context, _source, _hunts_enabled = fence_team_assessment(db, run_id=run_id)
    work.result_json = _reviewable_result(completion, messages, truncated=truncated)
    work.result_context_version = context.version
    work.result_source_version = source.source_version
    work.result_source_encrypted = work.source_encrypted
    work.result_article_id = source.article_id
    work.result_article_retrieved_at = source.article_retrieved_at
    work.generated_at = datetime.now(timezone.utc)
    finish_ai_task_run(
        db, run_id=run_id, status="ready", model=completion.model,
        prompt_tokens=completion.prompt_tokens, completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens, latency_ms=completion.latency_ms,
        prompt_char_count=completion.prompt_char_count, response_char_count=completion.response_char_count,
        input_text_chars=len(source.article_text),
        metadata_updates={"input_token_estimate": estimate_tokens(messages[1]["content"]), "source_truncated": truncated},
    )
    db.commit()
    return completion
