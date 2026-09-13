"""Cross-process admission; no provider requests run outside authorization fences."""
from __future__ import annotations

import logging
import uuid
import time
from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.ai_admission import provider_admission_engine
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.services.outbound_deadline import OutboundDeadlineExceeded, outbound_deadline_at
from app.models.ai_provider_budget import AIProviderBudgetReservation, AIProviderBudgetState
from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.report_prompt_budget import estimate_message_tokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIProviderBudgetLease:
    id: uuid.UUID
    deadline_monotonic: float


def provider_budget_key(active: ActiveAISettings) -> str:
    provider_id = getattr(active, "provider_id", None)
    return f"profile:{provider_id}" if provider_id is not None else "legacy"


def reserve_provider_budget(
    db: Session, active: ActiveAISettings, *, messages: list[dict[str, str]], requested_tokens: int,
) -> AIProviderBudgetLease | None:
    from app.services.ai_workflow_dispatch import AIWorkflowDeferred

    concurrent_limit = getattr(active, "max_concurrent_requests", 0)
    token_limit = getattr(active, "hourly_token_budget", 0)
    if not concurrent_limit and not token_limit:
        return None
    estimated = estimate_message_tokens(messages) + requested_tokens
    if token_limit and estimated > token_limit:
        raise AIIntegrationError(
            "This request exceeds the provider's entire hourly token budget. Reduce its input/output allowance or increase the budget.",
            retryable=False, provider_io_outcome="not_sent", failure_category="budget_request_too_large",
        )
    key = provider_budget_key(active)
    # Independent short transactions release admission locks before external I/O.
    # The dedicated pool cannot starve behind requests holding authorization
    # connections. It uses the caller's database, including isolated fixtures.
    try:
        with Session(bind=provider_admission_engine(db)) as budget_db, database_operation(
            budget_db, operation="interactive", timeout_seconds=3,
        ):
            budget_db.execute(insert(AIProviderBudgetState).values(provider_key=key).on_conflict_do_nothing())
            budget_db.scalar(select(AIProviderBudgetState).where(
                AIProviderBudgetState.provider_key == key
            ).with_for_update())
            lease_started = time.monotonic()
            now = budget_db.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            row = AIProviderBudgetReservation
            live = (row.completed_at.is_(None)) & (row.expires_at > now)
            active_count, first_expiry = budget_db.execute(select(func.count(), func.min(row.expires_at)).where(
                row.provider_key == key, live,
            )).one()
            if concurrent_limit and active_count >= concurrent_limit:
                raise AIWorkflowDeferred("provider_concurrency_budget", min(60.0, max(1.0, (first_expiry - now).total_seconds())))
            # Unknown outcomes retain the reserved estimate; known usage charges
            # actual total tokens. Provider profile edits do not reset history.
            charge = func.coalesce(row.charged_tokens, row.reserved_tokens)
            spent, first_created = budget_db.execute(select(func.coalesce(func.sum(charge), 0), func.min(row.created_at)).where(
                row.provider_key == key, row.created_at > now - timedelta(hours=1), charge > 0,
            )).one()
            if token_limit and spent + estimated > token_limit:
                retry_after = (first_created + timedelta(hours=1) - now).total_seconds() if first_created else 60.0
                raise AIWorkflowDeferred("provider_hourly_token_budget", max(1.0, retry_after))
            reservation = AIProviderBudgetReservation(
                provider_key=key, reserved_tokens=estimated,
                expires_at=now + timedelta(seconds=getattr(active, "request_timeout_seconds", 300) + 60),
                created_at=now,
            )
            budget_db.add(reservation)
            budget_db.flush()
            reservation_id = reservation.id
            # Globally prune at most 100 expired reservations per admission,
            # including retired/inactive profiles. Active leases and this hour's
            # accounting are never removed; the creation index bounds the scan.
            expired_ids = select(row.id).where(
                row.created_at < now - timedelta(hours=2), row.expires_at < now,
            ).order_by(row.created_at).limit(100)
            budget_db.execute(delete(row).where(row.id.in_(expired_ids)))
            budget_db.commit()
            return AIProviderBudgetLease(
                reservation_id, lease_started + getattr(active, "request_timeout_seconds", 300) + 60,
            )
    except (SQLAlchemyError, DatabaseDeadlineExceeded) as error:
        logger.warning("ai_provider_admission_unavailable error_type=%s", type(error).__name__)
        raise AIWorkflowDeferred("provider_budget_unavailable", 10.0) from error


def settle_provider_budget(
    db: Session, reservation_id: uuid.UUID | None, *, result: AICompletionResult | AIIntegrationError | None,
) -> None:
    if reservation_id is None:
        return
    try:
        with Session(bind=provider_admission_engine(db)) as budget_db, database_operation(
            budget_db, operation="interactive", timeout_seconds=3,
        ):
            reservation = budget_db.get(AIProviderBudgetReservation, reservation_id, with_for_update=True)
            if reservation is None or reservation.completed_at is not None:
                return
            outcome = result.provider_io_outcome if isinstance(result, AIIntegrationError) else (
                "response_received" if result is not None else "ambiguous"
            )
            total = result.total_tokens if result is not None else None
            if total is None and result is not None and result.prompt_tokens is not None and result.completion_tokens is not None:
                total = result.prompt_tokens + result.completion_tokens
            reservation.charged_tokens = 0 if outcome == "not_sent" else total
            reservation.outcome = outcome
            reservation.completed_at = datetime.now(timezone.utc)
            budget_db.commit()
    except (SQLAlchemyError, DatabaseDeadlineExceeded) as error:
        # Keep the lease/estimate conservative; a bookkeeping failure must not
        # turn a received provider completion into another paid request.
        logger.warning("ai_provider_budget_settlement_deferred reservation_id=%s error_type=%s", reservation_id, type(error).__name__)


def call_with_provider_budget(
    db: Session, active: ActiveAISettings, *, call: Callable[..., AICompletionResult],
    messages: list[dict[str, str]], requested_tokens: int, call_kwargs: dict,
) -> AICompletionResult:
    lease = reserve_provider_budget(db, active, messages=messages, requested_tokens=requested_tokens)
    if lease is None:
        return call(active, **call_kwargs)
    call_started = False
    result: AICompletionResult | AIIntegrationError | None = None
    try:
        with outbound_deadline_at(lease.deadline_monotonic):
            call_started = True
            result = call(active, **call_kwargs)
            return result
    except AIIntegrationError as error:
        result = error
        raise
    except OutboundDeadlineExceeded as error:
        result = AIIntegrationError(
            "The provider workload reservation expired. Review the task outcome before retrying.",
            retryable=False, failure_category="provider_admission_expired",
            provider_io_outcome="ambiguous" if call_started else "not_sent",
        )
        raise result from error
    finally:
        # Synchronous I/O has returned or raised before releasing the slot.
        settle_provider_budget(db, lease.id, result=result)
