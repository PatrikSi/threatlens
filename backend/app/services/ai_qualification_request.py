"""Dedicated synthetic qualification entrypoint retaining ordinary caller fences."""
import uuid
from collections.abc import Callable
from sqlalchemy.orm import Session
from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_integration import _request_json_with_usage

FEATURE_CONNECTION_TEST = "connection_test"


def request_ai_qualification_json(
    db: Session, active: ActiveAISettings, *, task_run_id: uuid.UUID,
    messages: list[dict[str, str]], provider_operation_scope: str,
    max_completion_tokens: int, max_retry_completion_tokens: int,
    max_provider_attempts: int, execution_checkpoint: Callable[[], None],
    request_authorization_check: Callable[[], None], feature_type: str,
) -> AICompletionResult:
    """Dedicated synthetic workflow; ordinary calls cannot bypass caller fences."""
    from app.models.ai_qualification import AIQualification
    if feature_type != FEATURE_CONNECTION_TEST or db.get(AIQualification, task_run_id) is None:
        raise AIIntegrationError("A durable provider qualification is required.", retryable=False, provider_io_outcome="not_sent")
    return _request_json_with_usage(db, active, feature_type=FEATURE_CONNECTION_TEST,
        messages=messages, task_run_id=task_run_id, provider_operation_scope=provider_operation_scope,
        max_completion_tokens=max_completion_tokens, max_retry_completion_tokens=max_retry_completion_tokens,
        max_provider_attempts=max_provider_attempts, execution_checkpoint=execution_checkpoint,
        request_authorization_check=request_authorization_check)
