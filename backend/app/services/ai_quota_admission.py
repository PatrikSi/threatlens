"""Account admission and leased round-robin turns, all before provider I/O."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.ai_provider_budget import AIProviderBudgetReservation
from app.models.ai_quota_group import AIQuotaGroup, AIQuotaGroupMember, AIQuotaTeamTurn
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred

TURN_LIFETIME_SECONDS = 180
MAX_TEAM_TURNS = 1000


@dataclass(frozen=True)
class BudgetScope:
    key: str
    predicate: object
    concurrent_limit: int
    token_limit: int


def group_scope(
    db: Session, provider_key: str
) -> tuple[AIQuotaGroup | None, BudgetScope | None]:
    group = db.scalar(
        select(AIQuotaGroup)
        .join(AIQuotaGroupMember)
        .where(AIQuotaGroupMember.provider_key == provider_key)
    )
    if group is None:
        return None, None
    key = f"account:{group.id}"
    members = select(AIQuotaGroupMember.provider_key).where(
        AIQuotaGroupMember.group_id == group.id
    )
    # Moving profiles does not reset accounting. Old groups retain their recorded
    # reservations; new groups inherit current members' rolling-hour charges.
    row = AIProviderBudgetReservation
    predicate = or_(row.quota_group_key == key, row.provider_key.in_(members))
    return group, BudgetScope(
        key, predicate, group.max_concurrent_requests, group.hourly_token_budget
    )


def check_scope(
    db: Session, scope: BudgetScope, *, now: datetime, estimated: int
) -> AIWorkflowDeferred | None:
    if scope.token_limit and estimated > scope.token_limit:
        raise AIIntegrationError(
            "This request exceeds the entire hourly token allowance for its provider or shared account. Reduce its input/output allowance or increase the budget.",
            retryable=False,
            provider_io_outcome="not_sent",
            failure_category="budget_request_too_large",
        )
    row = AIProviderBudgetReservation
    live = row.completed_at.is_(None) & (row.expires_at > now)
    active_count, first_expiry = db.execute(
        select(func.count(), func.min(row.expires_at)).where(scope.predicate, live)
    ).one()
    reason_prefix = "shared_account" if scope.key.startswith("account:") else "provider"
    if scope.concurrent_limit and active_count >= scope.concurrent_limit:
        return AIWorkflowDeferred(
            f"{reason_prefix}_concurrency_budget",
            min(60.0, max(1.0, (first_expiry - now).total_seconds())),
        )
    charge = func.coalesce(row.charged_tokens, row.reserved_tokens)
    spent, first_created = db.execute(
        select(func.coalesce(func.sum(charge), 0), func.min(row.created_at)).where(
            scope.predicate,
            row.created_at > now - timedelta(hours=1),
            charge > 0,
        )
    ).one()
    if scope.token_limit and spent + estimated > scope.token_limit:
        delay = (
            (first_created + timedelta(hours=1) - now).total_seconds()
            if first_created
            else 60.0
        )
        return AIWorkflowDeferred(
            f"{reason_prefix}_hourly_token_budget", max(1.0, delay)
        )
    return None


def fair_team_turn(
    db: Session,
    *,
    group: AIQuotaGroup,
    scope: BudgetScope,
    team_key: str,
    now: datetime,
    capacity_available: bool,
) -> AIWorkflowDeferred | None:
    """One expiring wait entry per team; an inactive team cannot block forever.

    A failed admission must commit its waiting turn before raising. Only an
    admitted request advances last_served_at. All contenders share the account
    state lock, so another team cannot take the same turn concurrently.
    """
    row = AIQuotaTeamTurn
    expired = (
        select(row.team_key)
        .where(
            row.group_id == group.id,
            or_(
                row.waiting_until <= now,
                row.waiting_until.is_(None)
                & (row.last_served_at < now - timedelta(hours=2)),
            ),
        )
        .order_by(row.team_key)
        .limit(100)
    )
    db.execute(delete(row).where(row.group_id == group.id, row.team_key.in_(expired)))
    turn = db.get(row, (group.id, team_key))
    if turn is None:
        count = (
            db.scalar(
                select(func.count()).select_from(row).where(row.group_id == group.id)
            )
            or 0
        )
        if count >= MAX_TEAM_TURNS:
            return AIWorkflowDeferred("shared_account_team_queue_full", 30)
        turn = row(group_id=group.id, team_key=team_key)
        db.add(turn)
    if (
        turn.waiting_since is None
        or turn.waiting_until is None
        or turn.waiting_until <= now
    ):
        turn.waiting_since = now
    turn.waiting_until = now + timedelta(seconds=TURN_LIFETIME_SECONDS)
    db.flush()
    if not capacity_available:
        return None
    reservation = AIProviderBudgetReservation
    live = reservation.completed_at.is_(None) & (reservation.expires_at > now)
    if group.max_concurrent_per_team:
        own_count = (
            db.scalar(
                select(func.count())
                .select_from(reservation)
                .where(
                    scope.predicate,
                    live,
                    reservation.team_key == team_key,
                )
            )
            or 0
        )
        if own_count >= group.max_concurrent_per_team:
            return AIWorkflowDeferred("shared_account_team_concurrency", 5)
    waiting = select(row.team_key).where(
        row.group_id == group.id, row.waiting_until > now
    )
    if group.max_concurrent_per_team:
        live_count = (
            select(func.count())
            .select_from(reservation)
            .where(
                scope.predicate,
                live,
                reservation.team_key == row.team_key,
            )
            .correlate(row)
            .scalar_subquery()
        )
        waiting = waiting.where(live_count < group.max_concurrent_per_team)
    next_team = db.scalar(
        waiting.order_by(
            row.last_served_at.asc().nullsfirst(), row.waiting_since, row.team_key
        ).limit(1)
    )
    if next_team != team_key:
        return AIWorkflowDeferred("shared_account_team_turn", 5)
    turn.last_served_at, turn.waiting_since, turn.waiting_until = now, None, None
    return None


def task_team_key(db: Session, task_run_id: uuid.UUID | None) -> str:
    from app.models.ai_task_run import AITaskRun

    if task_run_id is None:
        return "shared"
    run = db.get(AITaskRun, task_run_id)
    metadata = run.metadata_json if run is not None else None
    value = metadata.get("team_id") if isinstance(metadata, dict) else None
    if value is None:
        return "shared"
    try:
        return f"team:{uuid.UUID(str(value))}"
    except (ValueError, TypeError):
        raise AIIntegrationError(
            "The queued AI task has an invalid team allocation. Start a new task.",
            retryable=False,
            provider_io_outcome="not_sent",
        ) from None
