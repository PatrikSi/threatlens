from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.core.config import get_settings
from app.schemas.reports import (
    ReportArticleFilters,
    ReportCreateRequest,
    ReportPromptConfig,
    ReportScheduleCreate,
    ReportScheduleResponse,
    ReportScheduleUpdate,
    ReportSectionConfig,
    ReportSectionSetError,
    validate_report_section_set,
)
from app.services.ai_config import load_active_ai_settings
from app.services.ai_context_budget import AIContextBudgetError
from app.services.ai_prompting import build_company_context
from app.services.export_query import ExportSnapshotChangedError
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.report_sources import (
    build_report_source_plan,
    filters_for_report_period,
)
from app.services.report_storage import (
    ReportStorageError,
    create_report_from_plan,
    report_plan_record_fields,
)
from app.services.resource_versions import next_resource_version, resource_version_value


MAX_CATCH_UP_RUNS = 4
PERMANENT_SCHEDULE_FAILURE_ATTEMPTS = 3


@dataclass(frozen=True)
class ScheduleFailure:
    code: str
    message: str
    quarantine: bool = False


def create_report_schedule(
    db: Session,
    *,
    user_id: uuid.UUID,
    payload: ReportScheduleCreate,
) -> ReportSchedule:
    schedule = ReportSchedule(owner_user_id=user_id)
    apply_schedule_payload(schedule, payload)
    db.add(schedule)
    db.flush()
    return schedule


def apply_schedule_payload(
    schedule: ReportSchedule,
    payload: ReportScheduleCreate | ReportScheduleUpdate,
) -> None:
    schedule.template_id = payload.template_id
    schedule.name = payload.name
    schedule.enabled = payload.enabled
    schedule.cadence = payload.cadence
    schedule.day_of_week = payload.day_of_week
    schedule.day_of_month = payload.day_of_month
    schedule.hour = payload.hour
    schedule.minute = payload.minute
    schedule.timezone = payload.timezone
    schedule.window_type = payload.window_type
    schedule.rolling_days = payload.rolling_days
    schedule.filters_json = payload.filters.model_dump(mode="json")
    schedule.custom_instructions = payload.custom_instructions
    schedule.delivery_enabled = payload.delivery_enabled
    schedule.delivery_mode = payload.delivery_mode
    schedule.skip_empty = payload.skip_empty
    schedule.missed_run_policy = payload.missed_run_policy
    schedule.next_run_at = (
        next_schedule_run(schedule, after=datetime.now(timezone.utc))
        if payload.enabled
        else None
    )
    schedule.failure_state = "healthy"
    schedule.consecutive_failure_count = 0
    schedule.retry_at = None


def report_schedule_response(schedule: ReportSchedule) -> ReportScheduleResponse:
    return ReportScheduleResponse(
        id=schedule.id,
        owner_user_id=schedule.owner_user_id,
        template_id=schedule.template_id,
        name=schedule.name,
        enabled=schedule.enabled,
        cadence=schedule.cadence,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        hour=schedule.hour,
        minute=schedule.minute,
        timezone=schedule.timezone,
        window_type=schedule.window_type,
        rolling_days=schedule.rolling_days,
        filters=ReportArticleFilters.model_validate(schedule.filters_json or {}),
        custom_instructions=schedule.custom_instructions,
        delivery_enabled=schedule.delivery_enabled,
        delivery_mode=schedule.delivery_mode,
        skip_empty=schedule.skip_empty,
        missed_run_policy=schedule.missed_run_policy,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        failure_state=schedule.failure_state,
        failure_count=schedule.failure_count,
        consecutive_failure_count=schedule.consecutive_failure_count,
        last_error_code=schedule.last_error_code,
        last_error=schedule.last_error,
        last_error_at=schedule.last_error_at,
        retry_at=schedule.retry_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
        resource_version=resource_version_value(schedule.updated_at),
    )


def next_schedule_run(schedule: ReportSchedule, *, after: datetime) -> datetime:
    zone = ZoneInfo(schedule.timezone)
    local_after = _as_utc(after).astimezone(zone)
    if schedule.cadence == "weekly":
        days = (schedule.day_of_week - local_after.weekday()) % 7
        candidate_date = local_after.date() + timedelta(days=days)
        candidate = datetime.combine(
            candidate_date, time(schedule.hour, schedule.minute), tzinfo=zone
        )
        if candidate <= local_after:
            candidate += timedelta(days=7)
    else:
        year, month = local_after.year, local_after.month
        day = min(schedule.day_of_month, calendar.monthrange(year, month)[1])
        candidate = datetime(
            year, month, day, schedule.hour, schedule.minute, tzinfo=zone
        )
        if candidate <= local_after:
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            day = min(schedule.day_of_month, calendar.monthrange(year, month)[1])
            candidate = datetime(
                year, month, day, schedule.hour, schedule.minute, tzinfo=zone
            )
    return candidate.astimezone(timezone.utc)


def schedule_report_period(
    schedule: ReportSchedule, *, due_at: datetime
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(schedule.timezone)
    local_due = _as_utc(due_at).astimezone(zone)
    if schedule.window_type == "rolling_days":
        return _as_utc(due_at) - timedelta(days=schedule.rolling_days), _as_utc(due_at)
    if schedule.window_type == "previous_complete_month":
        month_start = datetime(local_due.year, local_due.month, 1, tzinfo=zone)
        previous_day = month_start - timedelta(days=1)
        previous_start = datetime(previous_day.year, previous_day.month, 1, tzinfo=zone)
        return previous_start.astimezone(timezone.utc), month_start.astimezone(
            timezone.utc
        )
    current_week_start = datetime.combine(
        local_due.date() - timedelta(days=local_due.weekday()),
        time.min,
        tzinfo=zone,
    )
    return (
        (current_week_start - timedelta(days=7)).astimezone(timezone.utc),
        current_week_start.astimezone(timezone.utc),
    )


def list_due_schedule_ids(
    db: Session,
    *,
    now: datetime,
    limit: int = 50,
) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(ReportSchedule.id)
            .where(
                ReportSchedule.enabled.is_(True),
                ReportSchedule.next_run_at.is_not(None),
                ReportSchedule.next_run_at <= _as_utc(now),
                or_(
                    ReportSchedule.retry_at.is_(None),
                    ReportSchedule.retry_at <= _as_utc(now),
                ),
            )
            .order_by(
                func.coalesce(
                    ReportSchedule.retry_at, ReportSchedule.next_run_at
                ).asc()
            )
            .limit(limit)
        ).all()
    )


def reserve_schedule_runs(
    db: Session,
    *,
    schedule_id: uuid.UUID,
    now: datetime,
    force: bool = False,
    generation_key_override: str | None = None,
    request_idempotency_key_hash: str | None = None,
    request_fingerprint: str | None = None,
) -> list[Report]:
    if generation_key_override is not None and not force:
        raise ValueError("A schedule generation-key override requires a forced run.")
    schedule = db.scalar(
        select(ReportSchedule).where(ReportSchedule.id == schedule_id).with_for_update()
    )
    if schedule is None:
        return []
    if not force and (
        not schedule.enabled
        or schedule.next_run_at is None
        or schedule.next_run_at > _as_utc(now)
    ):
        return []
    if schedule.owner_user_id is None:
        _quarantine_schedule(
            schedule,
            now=now,
            code="owner_missing",
            message="The schedule owner no longer exists.",
        )
        return []
    template = db.get(ReportTemplate, schedule.template_id)
    if template is None:
        _quarantine_schedule(
            schedule,
            now=now,
            code="template_missing",
            message="The report template no longer exists.",
        )
        return []

    due_times = [_as_utc(now) if force else _as_utc(schedule.next_run_at)]
    if not force and schedule.missed_run_policy == "all":
        cursor = next_schedule_run(schedule, after=due_times[0])
        while cursor <= _as_utc(now) and len(due_times) < MAX_CATCH_UP_RUNS:
            due_times.append(cursor)
            cursor = next_schedule_run(schedule, after=cursor)
    elif not force and schedule.missed_run_policy == "skip":
        due_times = []
    elif not force and schedule.missed_run_policy == "latest":
        due_times = [_as_utc(now)]

    reports: list[Report] = []
    for due_at in due_times:
        report = _create_one_scheduled_report(
            db,
            schedule=schedule,
            template=template,
            due_at=due_at,
            generation_key_override=generation_key_override,
            request_idempotency_key_hash=request_idempotency_key_hash,
            request_fingerprint=request_fingerprint,
        )
        if report is not None:
            reports.append(report)
    schedule.last_run_at = _as_utc(now) if due_times else schedule.last_run_at
    schedule.next_run_at = (
        next_schedule_run(schedule, after=_as_utc(now)) if schedule.enabled else None
    )
    schedule.failure_state = "healthy"
    schedule.consecutive_failure_count = 0
    schedule.retry_at = None
    schedule.updated_at = next_resource_version(schedule.updated_at)
    db.add(schedule)
    return reports


def _create_one_scheduled_report(
    db: Session,
    *,
    schedule: ReportSchedule,
    template: ReportTemplate,
    due_at: datetime,
    generation_key_override: str | None = None,
    request_idempotency_key_hash: str | None = None,
    request_fingerprint: str | None = None,
) -> Report | None:
    period_start, period_end = schedule_report_period(schedule, due_at=due_at)
    generation_key = generation_key_override or (
        f"schedule:{schedule.id}:{period_start.isoformat()}:{period_end.isoformat()}"
    )
    existing = db.scalar(select(Report).where(Report.generation_key == generation_key))
    if existing is not None:
        return None
    prompt = ReportPromptConfig(
        audience=template.audience,
        objective=template.objective,
        tone=template.tone,
        detail_level=template.detail_level,
        use_company_context=template.use_company_context,
        custom_instructions=_join_instructions(
            template.custom_instructions, schedule.custom_instructions
        ),
        focus_topics=list(template.focus_topics_json or []),
        excluded_topics=list(template.excluded_topics_json or []),
    )
    sections = [
        ReportSectionConfig.model_validate(entry)
        for entry in template.sections_json or []
    ]
    validate_report_section_set(sections)
    filters = filters_for_report_period(
        ReportArticleFilters.model_validate(schedule.filters_json or {}),
        period_start=period_start,
        period_end=period_end,
    )
    active = load_active_ai_settings(db)
    ensure_reporting_available(active)
    plan = build_report_source_plan(
        db,
        user_id=schedule.owner_user_id,
        filters=filters,
        excluded_item_ids=[],
        prompt=prompt,
        sections=sections,
        active=active,
    )
    payload = ReportCreateRequest(
        template_id=template.id,
        period_start=period_start,
        period_end=period_end,
        filters=filters,
        prompt=prompt,
        sections=sections,
        deliver_when_ready=schedule.delivery_enabled,
        delivery_mode=schedule.delivery_mode,
    )
    try:
        with db.begin_nested():
            return create_report_from_plan(
                db,
                user_id=schedule.owner_user_id,
                payload=payload,
                plan=plan,
                template=template,
                active=active,
                trigger_source="scheduled",
                schedule_id=schedule.id,
                generation_key=generation_key,
                request_idempotency_key_hash=request_idempotency_key_hash,
                request_fingerprint=request_fingerprint,
            )
    except IntegrityError as exc:
        if _integrity_constraint_name(exc) in {
            "reports_generation_key_key",
            "uq_reports_owner_request_idempotency_key_hash",
        }:
            return None
        raise
    except ReportStorageError:
        if not schedule.skip_empty:
            raise
        error_code = "no_sources" if plan.total_matches == 0 else "context_budget"
        error = (
            "No matching source articles were available for the scheduled period."
            if plan.total_matches == 0
            else "Matching source articles did not fit the configured AI context budget."
        )
        report = Report(
            template_id=template.id,
            schedule_id=schedule.id,
            owner_user_id=schedule.owner_user_id,
            title=f"{template.name}: {period_start.date()} to {period_end.date()}",
            report_type=template.report_type,
            status="skipped",
            trigger_source="scheduled",
            generation_stage="skipped",
            generation_key=generation_key,
            request_idempotency_key_hash=request_idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            period_start=period_start,
            period_end=period_end,
            filters_json=filters.model_dump(mode="json"),
            prompt_config_json=prompt.model_dump(mode="json"),
            generation_context_json={
                "company_context": build_company_context(active)
                if prompt.use_company_context
                else {},
                "global_instructions": active.global_instructions,
            },
            sections_config_json=[
                section.model_dump(mode="json") for section in sections
            ],
            **report_plan_record_fields(plan),
            provider=active.provider_type,
            model=active.model,
            error_code=error_code,
            error=error,
            delivery_mode=schedule.delivery_mode,
        )
        try:
            with db.begin_nested():
                db.add(report)
                db.flush()
        except IntegrityError:
            return None
        return report


def _join_instructions(*values: str | None) -> str | None:
    result = "\n".join(value.strip() for value in values if value and value.strip())
    return result or None


def record_schedule_failure(
    db: Session,
    *,
    schedule_id: uuid.UUID,
    now: datetime,
    error: Exception,
) -> ReportSchedule | None:
    schedule = db.scalar(
        select(ReportSchedule)
        .where(ReportSchedule.id == schedule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if schedule is None:
        return None

    failure = classify_schedule_failure(error)
    settings = get_settings()
    observed_at = _as_utc(now)
    schedule.failure_count = int(schedule.failure_count or 0) + 1
    schedule.consecutive_failure_count = (
        int(schedule.consecutive_failure_count or 0) + 1
    )
    schedule.last_error_code = failure.code[:64]
    schedule.last_error = failure.message[:4000]
    schedule.last_error_at = observed_at

    max_attempts = (
        min(settings.report_schedule_max_attempts, PERMANENT_SCHEDULE_FAILURE_ATTEMPTS)
        if failure.quarantine
        else settings.report_schedule_max_attempts
    )
    if schedule.consecutive_failure_count >= max_attempts:
        schedule.retry_at = None
        if failure.quarantine:
            schedule.enabled = False
            schedule.next_run_at = None
            schedule.failure_state = "quarantined"
        else:
            schedule.failure_state = "exhausted"
            schedule.consecutive_failure_count = 0
            try:
                schedule.next_run_at = (
                    next_schedule_run(schedule, after=observed_at)
                    if schedule.enabled
                    else None
                )
            except (ZoneInfoNotFoundError, AttributeError, TypeError, ValueError):
                schedule.enabled = False
                schedule.next_run_at = None
                schedule.retry_at = None
                schedule.failure_state = "quarantined"
                schedule.last_error_code = "invalid_configuration"
                schedule.last_error = (
                    "The schedule contains invalid timing configuration. "
                    "Update it before re-enabling the schedule."
                )
    else:
        exponent = max(0, schedule.consecutive_failure_count - 1)
        delay_seconds = min(
            settings.report_schedule_retry_max_backoff_seconds,
            settings.report_schedule_retry_backoff_seconds * (2**exponent),
        )
        schedule.failure_state = "retrying"
        schedule.retry_at = observed_at + timedelta(seconds=delay_seconds)
    schedule.updated_at = next_resource_version(
        schedule.updated_at,
        observed_at=observed_at,
    )
    db.add(schedule)
    return schedule


def classify_schedule_failure(error: Exception) -> ScheduleFailure:
    if isinstance(error, ReportingUnavailableError):
        return ScheduleFailure(error.code, str(error))
    if isinstance(error, AIContextBudgetError):
        return ScheduleFailure("context_budget", str(error), quarantine=True)
    if isinstance(error, (ValidationError, ReportSectionSetError)):
        return ScheduleFailure(
            "invalid_configuration",
            "The schedule template contains invalid report configuration. Update the template and re-enable the schedule.",
            quarantine=True,
        )
    if isinstance(error, ExportSnapshotChangedError):
        return ScheduleFailure(
            "source_snapshot_changed",
            "Matching articles changed while the scheduled report was being prepared.",
        )
    if isinstance(error, ReportStorageError):
        return ScheduleFailure("source_selection", str(error))
    if isinstance(
        error,
        (ZoneInfoNotFoundError, AttributeError, TypeError, ValueError),
    ):
        return ScheduleFailure(
            "invalid_configuration",
            "The schedule contains invalid timing or report configuration. Update it and re-enable the schedule.",
            quarantine=True,
        )
    return ScheduleFailure(
        "reservation_failed",
        "Scheduled report preparation failed unexpectedly. Review the maintenance worker logs before retrying.",
    )


def _quarantine_schedule(
    schedule: ReportSchedule,
    *,
    now: datetime,
    code: str,
    message: str,
) -> None:
    schedule.enabled = False
    schedule.next_run_at = None
    schedule.retry_at = None
    schedule.failure_state = "quarantined"
    schedule.failure_count = int(schedule.failure_count or 0) + 1
    schedule.consecutive_failure_count = int(schedule.consecutive_failure_count or 0) + 1
    schedule.last_error_code = code
    schedule.last_error = message
    schedule.last_error_at = _as_utc(now)
    schedule.updated_at = next_resource_version(
        schedule.updated_at,
        observed_at=now,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
