"""Validate usable feature output before publishing a successful provider attempt."""
from dataclasses import replace

from app.core.config import get_settings
from app.services.ai_config import ActiveAISettings
from app.services.ai_normalization import coerce_score
from app.services.ai_output_storage import AIOutputStorageError, optional_storage_text, validate_output_storage
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.report_grounding import ReportGroundingError, report_stage_input, validate_stage_output


def normalize_completion_metadata(active: ActiveAISettings, completion: AICompletionResult) -> AICompletionResult:
    """Invalid optional provider metadata cannot discard received feature output."""
    return replace(
        completion,
        model=(optional_storage_text(completion.model, limit=255) or active.model) if completion.model is not None else None,
        provider=optional_storage_text(completion.provider, limit=64) or active.provider_type,
        finish_reason=optional_storage_text(completion.finish_reason, limit=255),
    )


def validate_feature_completion(
    active: ActiveAISettings, *, feature_type: str, completion: AICompletionResult,
    messages: list[dict[str, str]] | None = None,
) -> None:
    try:
        validate_output_storage(completion.payload, max_bytes=get_settings().ai_response_max_bytes)
        invalid_fields = _invalid_feature_fields(active, feature_type, completion.payload, messages)
    except AIOutputStorageError as error:
        # Do not run feature parsers on invalid or excessively nested payloads.
        invalid_fields = [str(error)]
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
        failure_category="invalid_output",
        provider_io_outcome="response_received",
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        latency_ms=completion.latency_ms,
    )


def _invalid_feature_fields(
    active: ActiveAISettings, feature_type: str, payload: dict[str, object], messages: list[dict[str, str]] | None,
) -> list[str]:
    invalid_fields: list[str] = []
    if feature_type == "item_enrichment":
        if active.summary_enabled and not _has_text(payload.get("summary_text")):
            invalid_fields.append("summary_text (nonempty text)")
        if active.relevance_enabled and coerce_score(payload.get("relevance_score")) is None:
            invalid_fields.append("relevance_score (finite number)")
    elif feature_type == "daily_brief":
        if not _has_text(payload.get("brief_text")):
            invalid_fields.append("brief_text (nonempty text)")
        title = payload.get("title")
        if title is not None and (not isinstance(title, str) or len(title.strip()) > 255):
            invalid_fields.append("title (text of at most 255 characters)")
    elif feature_type == "report" and messages:
        stage = report_stage_input(messages)
        if stage is not None:
            try:
                validate_stage_output(payload, stage=stage)
            except ReportGroundingError as error:
                invalid_fields.append(str(error))
    return invalid_fields


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
