"""Settle the bounded diagnostic consistently for both provider settings paths."""
import uuid

from sqlalchemy.orm import Session

from app.schemas.ai import AITestConnectionResponse
from app.services.ai_ops import finish_ai_task_run
from app.services.audit import record_audit


def finish_connection_test(
    db: Session, *, run_id: uuid.UUID, result: AITestConnectionResponse,
    actor_user_id: uuid.UUID, provider: tuple[uuid.UUID, int] | None = None,
) -> None:
    finish_ai_task_run(
        db, run_id=run_id,
        status="skipped" if result.skipped else "ready" if result.success else "error",
        reason=result.skip_reason if result.skipped else None if result.success else (
            "connection_test_failed" if provider is not None else "unexpected_response"
        ),
        error=result.error, worker_name="api", model=result.model, latency_ms=result.latency_ms,
    )
    metadata = (
        {"version": provider[1], "run_id": str(run_id)} if provider is not None
        else {"model": result.model, "latency_ms": result.latency_ms, "run_id": str(run_id)}
    )
    record_audit(
        db, actor_user_id=actor_user_id,
        action="ai.provider.test" if provider is not None else "ai.connection.test",
        resource_type="ai_provider" if provider is not None else "ai_settings",
        resource_id=str(provider[0]) if provider is not None else None,
        success=result.success, metadata=metadata,
    )
    db.commit()
