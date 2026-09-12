"""Validate usable feature output before publishing a successful provider attempt."""

from app.services.ai_config import ActiveAISettings
from app.services.ai_normalization import coerce_score
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.report_grounding import ReportGroundingError, report_stage_input, validate_stage_output


def validate_feature_completion(
    active: ActiveAISettings, *, feature_type: str, completion: AICompletionResult,
    messages: list[dict[str, str]] | None = None,
) -> None:
    payload = completion.payload
    invalid_fields: list[str] = []
    if feature_type == "item_enrichment":
        if active.summary_enabled and not _has_text(payload.get("summary_text")):
            invalid_fields.append("summary_text (nonempty text)")
        if active.relevance_enabled and coerce_score(payload.get("relevance_score")) is None:
            invalid_fields.append("relevance_score (finite number)")
    elif feature_type == "daily_brief":
        if not _has_text(payload.get("brief_text")):
            invalid_fields.append("brief_text (nonempty text)")
    elif feature_type == "report" and messages:
        stage = report_stage_input(messages)
        if stage is not None:
            try:
                validate_stage_output(payload, stage=stage)
            except ReportGroundingError as error:
                invalid_fields.append(str(error))
    if not invalid_fields:
        return
    raise AIIntegrationError(
        "AI response did not contain valid " + ", ".join(invalid_fields) + ".",
        request_url=completion.request_url,
        request_payload=completion.request_payload,
        response_body=completion.response_body,
        response_json=completion.response_json,
        status_code=completion.status_code,
        retryable=True,
        provider_io_outcome="response_received",
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        latency_ms=completion.latency_ms,
    )


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
