"""A bounded diagnostic result, including explicit workload admission deferral."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.schemas.ai import AITestConnectionResponse
from app.services.ai_config import ActiveAISettings, load_active_ai_settings
from app.services.ai_connection_diagnostics import connection_test_error
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred
from app.services.authorization import AuthorizationContext


def run_connection_test(
    db: Session, *, request_json: Callable[..., AICompletionResult], task_run_id: uuid.UUID | None = None,
    active_settings: ActiveAISettings | None = None,
    request_authorization: AuthorizationContext | None = None,
) -> AITestConnectionResponse:
    active = active_settings or load_active_ai_settings(db, use_legacy=True)
    if not active.ai_enabled:
        raise AIIntegrationError("AI features are disabled")
    if not active.ai_configured:
        raise AIIntegrationError(
            active.configuration_error or "Configure the AI base URL and model before testing the connection"
        )

    try:
        completion = request_json(
            db,
            active,
            feature_type="connection_test",
            task_run_id=task_run_id,
            provider_operation_scope="connection_test",
            request_authorization=request_authorization,
            messages=[
                {
                    "role": "system",
                    "content": "Return only JSON. Do not include markdown code fences.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "connection_test",
                            "instructions": 'Return {"ok": true, "message": "ready"}.',
                        }
                    ),
                },
            ],
        )
    except AIWorkflowDeferred as exc:
        return AITestConnectionResponse(success=False, latency_ms=None,
            provider=active.provider_type, model=active.model, skipped=True, skip_reason=exc.reason,
            error=f"Provider workload budget is busy. Retry after {max(1, int(exc.retry_after_seconds))} seconds; no request was sent.")
    except AIIntegrationError as exc:
        return AITestConnectionResponse(
            success=False,
            latency_ms=None,
            provider="openai_compatible",
            model=active.model,
            error=connection_test_error(active, exc),
        )

    return AITestConnectionResponse(
        success=bool(completion.payload.get("ok") is True),
        latency_ms=completion.latency_ms,
        provider="openai_compatible",
        model=completion.model,
        error=None
        if completion.payload.get("ok") is True
        else "Unexpected response from AI endpoint",
    )

