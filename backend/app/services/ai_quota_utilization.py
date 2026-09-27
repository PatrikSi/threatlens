"""Bounded database aggregation of local reservations, not upstream billing."""

from datetime import datetime, timedelta, timezone
import uuid
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.models.ai_provider_budget import AIProviderBudgetReservation
from app.models.ai_quota_group import AIQuotaGroup, AIQuotaGroupMember, AIQuotaTeamTurn
from app.schemas.ai_quota_groups import AIQuotaUsage, AIQuotaUtilization
from app.services.ai_providers import AIProviderError


def quota_utilization(db: Session, group_id: uuid.UUID) -> AIQuotaUtilization:
    if db.get(AIQuotaGroup, group_id) is None:
        raise AIProviderError(
            "quota_group_not_found",
            "Quota group not found. Reload AI settings.",
            status_code=404,
        )
    now = datetime.now(timezone.utc)
    row = AIProviderBudgetReservation
    members = select(AIQuotaGroupMember.provider_key).where(
        AIQuotaGroupMember.group_id == group_id
    )
    scope = or_(
        row.quota_group_key == f"account:{group_id}", row.provider_key.in_(members)
    )
    live = row.completed_at.is_(None) & (row.expires_at > now)
    minute = (row.created_at > now - timedelta(minutes=1)) & (
        row.outcome.is_(None) | (row.outcome != "not_sent")
    )
    aggregates = [
        func.count().filter(live).label("active_requests"),
        func.count().filter(minute).label("requests_last_minute"),
        func.coalesce(func.sum(row.reserved_tokens), 0).label(
            "reserved_tokens_last_hour"
        ),
        func.coalesce(func.sum(row.charged_tokens), 0).label(
            "reported_tokens_last_hour"
        ),
        func.coalesce(
            func.sum(func.coalesce(row.charged_tokens, row.reserved_tokens)), 0
        ).label("conservative_tokens_last_hour"),
    ]
    criteria = (scope, row.created_at > now - timedelta(hours=1))
    total = db.execute(select(*aggregates).where(*criteria)).one()
    usage = db.execute(
        select(row.team_key, *aggregates)
        .where(*criteria)
        .group_by(row.team_key)
        .order_by(row.team_key)
        .limit(201)
    ).all()
    waits = db.execute(
        select(
            AIQuotaTeamTurn.team_key,
            AIQuotaTeamTurn.waiting_since,
            AIQuotaTeamTurn.last_denial_reason,
        )
        .where(
            AIQuotaTeamTurn.group_id == group_id,
            AIQuotaTeamTurn.waiting_until > now,
        )
        .order_by(AIQuotaTeamTurn.waiting_since, AIQuotaTeamTurn.team_key)
        .limit(201)
    ).all()
    entries = {
        entry.team_key or "shared": AIQuotaUsage(
            team_key=entry.team_key or "shared",
            **{
                key: value for key, value in entry._mapping.items() if key != "team_key"
            },
        )
        for entry in usage[:200]
    }
    for wait in waits[:200]:
        if wait.team_key not in entries and len(entries) < 200:
            entries[wait.team_key] = AIQuotaUsage(
                team_key=wait.team_key,
                active_requests=0,
                requests_last_minute=0,
                reserved_tokens_last_hour=0,
                reported_tokens_last_hour=0,
                conservative_tokens_last_hour=0,
            )
        if wait.team_key in entries:
            entries[wait.team_key].waiting_seconds = (
                max(0, int((now - wait.waiting_since).total_seconds()))
                if wait.waiting_since
                else None
            )
            entries[wait.team_key].deferral_reason = wait.last_denial_reason
    oldest = (
        max(0, int((now - waits[0].waiting_since).total_seconds()))
        if waits and waits[0].waiting_since
        else None
    )
    return AIQuotaUtilization(
        group_id=group_id,
        totals=AIQuotaUsage(team_key="all", **total._mapping),
        teams=list(entries.values()),
        teams_truncated=len(usage) > 200
        or len(waits) > 200
        or len(set(entries) | {wait.team_key for wait in waits}) > 200,
        oldest_wait_seconds=oldest,
    )
