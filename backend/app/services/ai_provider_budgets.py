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
from app.services.outbound_deadline import (
    OutboundDeadlineExceeded,
    outbound_deadline_at,
)
from app.models.ai_provider_budget import (
    AIProviderBudgetReservation,
    AIProviderBudgetState,
)
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
    db: Session,
    active: ActiveAISettings,
    *,
    messages: list[dict[str, str]],
    requested_tokens: int,
    team_key: str = "shared",
) -> AIProviderBudgetLease | None:
    from app.services.ai_workflow_dispatch import AIWorkflowDeferred
    from app.services.ai_quota_groups import lock_quota_configuration
    from app.services.ai_quota_admission import (
        BudgetScope,
        check_scope,
        fair_team_turn,
        group_scope,
    )

    concurrent_limit = getattr(active, "max_concurrent_requests", 0)
    token_limit = getattr(active, "hourly_token_budget", 0)
    estimated = estimate_message_tokens(messages) + requested_tokens
    key = provider_budget_key(active)
    try:
        with (
            Session(bind=provider_admission_engine(db)) as budget_db,
            database_operation(
                budget_db,
                operation="interactive",
                timeout_seconds=3,
            ),
        ):
            # Configuration cannot move memberships until admission commits.
            # This shared lock never spans provider I/O or enters IAM/profile locks.
            lock_quota_configuration(budget_db)
            group, account_scope = group_scope(budget_db, key)
            if not concurrent_limit and not token_limit and group is None:
                return None
            row = AIProviderBudgetReservation
            profile_scope = BudgetScope(
                key, row.provider_key == key, concurrent_limit, token_limit
            )
            scopes = [profile_scope, *([account_scope] if account_scope else [])]
            for scope in sorted(scopes, key=lambda entry: entry.key):
                budget_db.execute(
                    insert(AIProviderBudgetState)
                    .values(provider_key=scope.key)
                    .on_conflict_do_nothing()
                )
                budget_db.scalar(
                    select(AIProviderBudgetState)
                    .where(
                        AIProviderBudgetState.provider_key == scope.key,
                    )
                    .with_for_update()
                )
            lease_started = time.monotonic()
            now = budget_db.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            profile_denial = check_scope(
                budget_db, profile_scope, now=now, estimated=estimated
            )
            denial = profile_denial or (
                check_scope(budget_db, account_scope, now=now, estimated=estimated)
                if account_scope
                else None
            )
            team_denial = None
            if group is not None and account_scope is not None:
                allocation = (group.team_hourly_token_budgets or {}).get(team_key, 0)
                if allocation:
                    team_scope = BudgetScope(
                        f"team-account:{group.id}:{team_key}",
                        account_scope.predicate & (row.team_key == team_key),
                        0,
                        allocation,
                    )
                    team_denial = check_scope(
                        budget_db, team_scope, now=now, estimated=estimated
                    )
                    denial = denial or team_denial
            if (
                group is not None
                and account_scope is not None
                and profile_denial is None
                and team_denial is None
            ):
                denial = (
                    fair_team_turn(
                        budget_db,
                        group=group,
                        scope=account_scope,
                        team_key=team_key,
                        now=now,
                        capacity_available=denial is None,
                    )
                    or denial
                )
            elif group is not None and account_scope is not None:
                # Keep diagnostics for locally blocked teams while excluding
                # their turn from admission competition below.
                fair_team_turn(
                    budget_db,
                    group=group,
                    scope=account_scope,
                    team_key=team_key,
                    now=now,
                    capacity_available=False,
                )
            if denial is not None:
                if group is not None:
                    from app.models.ai_quota_group import AIQuotaTeamTurn

                    turn = budget_db.get(AIQuotaTeamTurn, (group.id, team_key))
                    if turn is not None:
                        turn.last_denial_reason = str(profile_denial or team_denial or denial)[:80]
                budget_db.commit()
                raise denial
            reservation = AIProviderBudgetReservation(
                provider_key=key,
                quota_group_key=account_scope.key if account_scope else None,
                team_key=team_key,
                reserved_tokens=estimated,
                expires_at=now
                + timedelta(
                    seconds=getattr(active, "request_timeout_seconds", 300) + 60
                ),
                created_at=now,
            )
            budget_db.add(reservation)
            budget_db.flush()
            reservation_id = reservation.id
            expired_ids = (
                select(row.id)
                .where(
                    row.created_at < now - timedelta(hours=2),
                    row.expires_at < now,
                )
                .order_by(row.created_at)
                .limit(100)
            )
            budget_db.execute(delete(row).where(row.id.in_(expired_ids)))
            budget_db.commit()
            return AIProviderBudgetLease(
                reservation_id,
                lease_started + getattr(active, "request_timeout_seconds", 300) + 60,
            )
    except (SQLAlchemyError, DatabaseDeadlineExceeded) as error:
        logger.warning(
            "ai_provider_admission_unavailable error_type=%s", type(error).__name__
        )
        raise AIWorkflowDeferred("provider_budget_unavailable", 10.0) from error


def settle_provider_budget(
    db: Session,
    reservation_id: uuid.UUID | None,
    *,
    result: AICompletionResult | AIIntegrationError | None,
) -> None:
    if reservation_id is None:
        return
    try:
        with (
            Session(bind=provider_admission_engine(db)) as budget_db,
            database_operation(
                budget_db,
                operation="interactive",
                timeout_seconds=3,
            ),
        ):
            reservation = budget_db.get(
                AIProviderBudgetReservation, reservation_id, with_for_update=True
            )
            if reservation is None or reservation.completed_at is not None:
                return
            outcome = (
                result.provider_io_outcome
                if isinstance(result, AIIntegrationError)
                else ("response_received" if result is not None else "ambiguous")
            )
            total = result.total_tokens if result is not None else None
            if (
                total is None
                and result is not None
                and result.prompt_tokens is not None
                and result.completion_tokens is not None
            ):
                total = result.prompt_tokens + result.completion_tokens
            reservation.charged_tokens = 0 if outcome == "not_sent" else total
            reservation.outcome = outcome
            reservation.completed_at = datetime.now(timezone.utc)
            budget_db.commit()
    except (SQLAlchemyError, DatabaseDeadlineExceeded) as error:
        # Keep the lease/estimate conservative; a bookkeeping failure must not
        # turn a received provider completion into another paid request.
        logger.warning(
            "ai_provider_budget_settlement_deferred reservation_id=%s error_type=%s",
            reservation_id,
            type(error).__name__,
        )


def call_with_provider_budget(
    db: Session,
    active: ActiveAISettings,
    *,
    call: Callable[..., AICompletionResult],
    messages: list[dict[str, str]],
    requested_tokens: int,
    call_kwargs: dict,
    task_run_id: uuid.UUID | None = None,
) -> AICompletionResult:
    from app.services.ai_quota_admission import task_team_key
    from app.services.team_ai_destination_runtime import enforce_task_team_destination

    enforce_task_team_destination(
        db, task_run_id=task_run_id, provider_key=provider_budget_key(active)
    )
    lease = reserve_provider_budget(
        db,
        active,
        messages=messages,
        requested_tokens=requested_tokens,
        team_key=task_team_key(db, task_run_id),
    )
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
            retryable=False,
            failure_category="provider_admission_expired",
            provider_io_outcome="ambiguous" if call_started else "not_sent",
        )
        raise result from error
    finally:
        # Synchronous I/O has returned or raised before releasing the slot.
        settle_provider_budget(db, lease.id, result=result)
