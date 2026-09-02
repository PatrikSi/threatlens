from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.lifecycle import (
    LifecycleCatalogState,
    LifecyclePolicy,
    LifecyclePreview,
    LifecycleRun,
)
from app.services.audit import record_audit
from app.services.alert_maintenance import maintain_alert_history
from app.services.data_access_retention import prune_orphan_data_access_envelopes
from app.services.integration_maintenance import rollup_terminal_integration_deliveries
from app.services.lifecycle import LifecycleConflict, ensure_lifecycle_policies
from app.services.lifecycle_catalog import TARGETS, next_scheduled_at, target_definition
from app.services.lifecycle_targets import (
    execute_lifecycle_target_batch,
    preview_lifecycle_target,
)
from app.services.lifecycle_dependencies import (
    MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
)
from app.services.local_mfa import (
    cleanup_mfa_challenges,
    cleanup_pending_totp_enrollments,
)


logger = logging.getLogger("threatlens.lifecycle")
RUN_LEASE_TTL = timedelta(minutes=5)
PUBLICATION_STALE_AFTER = timedelta(minutes=5)
EXECUTION_BATCH_SIZE = 100
DISPATCH_LIMIT = 100
HOUSEKEEPING_BATCH_SIZE = 1_000
MAX_BATCHES_PER_INVOCATION = 3
MAX_INVOCATION_SECONDS = 5.0
MAX_NO_PROGRESS_RETRIES = 2


def dispatch_due_lifecycle_runs(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    current_time = _utc(now)
    try:
        ensure_lifecycle_policies(db, now=current_time, commit_missing=True)
    except LifecycleConflict as exc:
        db.rollback()
        _fence_active_runs_for_invalid_catalog(
            db,
            now=current_time,
            failure_code=exc.error_code,
        )
        db.commit()
        logger.error(
            "lifecycle_catalog_integrity_failure active_runs_fenced=true error_code=%s",
            exc.error_code,
        )
        return []
    _recover_stale_runs(db, now=current_time)
    due = list(
        db.scalars(
            select(LifecyclePolicy)
            .where(
                LifecyclePolicy.enabled.is_(True),
                LifecyclePolicy.next_run_at.is_not(None),
                LifecyclePolicy.next_run_at <= current_time,
            )
            .order_by(LifecyclePolicy.next_run_at, LifecyclePolicy.target_key)
            .limit(DISPATCH_LIMIT)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for policy in due:
        scheduled_for = _utc(policy.next_run_at)
        active = db.scalar(
            select(LifecycleRun.id).where(
                LifecycleRun.target_key == policy.target_key,
                LifecycleRun.status.in_(("queued", "running")),
            )
        )
        existing_tick = db.scalar(
            select(LifecycleRun.id).where(
                LifecycleRun.target_key == policy.target_key,
                LifecycleRun.scheduled_for == scheduled_for,
            )
        )
        if active is None and existing_tick is None:
            snapshot = _policy_snapshot(policy)
            run = LifecycleRun(
                target_key=policy.target_key,
                trigger_source="scheduled",
                status="queued",
                policy_revision=policy.revision,
                policy_snapshot_json=snapshot,
                preview_id=None,
                requested_by_user_id=None,
                reason="Scheduled lifecycle policy execution.",
                cutoff_at=scheduled_for - timedelta(days=policy.retention_days),
                scheduled_for=scheduled_for,
                max_records=policy.max_records_per_run,
                queued_at=current_time,
            )
            db.add(run)
            db.flush()
            policy.last_run_at = current_time
            policy.last_run_status = "queued"
        policy.next_run_at = next_scheduled_at(
            cadence=policy.schedule_cadence,
            hour_utc=policy.schedule_hour_utc,
            weekday=policy.schedule_weekday,
            after=max(current_time, scheduled_for),
        )
        db.add(policy)
    db.commit()
    queued_runs = list(
        db.scalars(
            select(LifecycleRun)
            .where(
                LifecycleRun.status == "queued",
                or_(
                    LifecycleRun.celery_task_id.is_(None),
                    LifecycleRun.updated_at <= current_time - PUBLICATION_STALE_AFTER,
                ),
            )
            .order_by(LifecycleRun.queued_at, LifecycleRun.id)
            .limit(DISPATCH_LIMIT)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for queued_run in queued_runs:
        if queued_run.celery_task_id is not None:
            details = dict(queued_run.details_json or {})
            details["publication_recovery_count"] = (
                int(details.get("publication_recovery_count") or 0) + 1
            )
            queued_run.details_json = details
            db.add(queued_run)
    db.commit()
    return [run.id for run in queued_runs]


def mark_lifecycle_run_published(
    db: Session,
    *,
    run_id: uuid.UUID,
    celery_task_id: str,
) -> tuple[str | None, bool]:
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None or run.status != "queued":
        db.rollback()
        return None, False
    newly_reserved = run.celery_task_id is None
    durable_task_id = run.celery_task_id or celery_task_id
    run.celery_task_id = durable_task_id
    db.add(run)
    db.commit()
    return durable_task_id, newly_reserved


def release_lifecycle_run_publication(
    db: Session,
    *,
    run_id: uuid.UUID,
    celery_task_id: str,
) -> None:
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if (
        run is not None
        and run.status == "queued"
        and run.celery_task_id == celery_task_id
    ):
        run.celery_task_id = None
        db.add(run)
        db.commit()


def execute_lifecycle_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    expected_task_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    current_time = _utc(now)
    lease_token = secrets.token_hex(24)
    run = _claim_run(
        db,
        run_id=run_id,
        lease_token=lease_token,
        expected_task_id=expected_task_id,
        now=current_time,
    )
    if run is None:
        return {"status": "ignored", "run_id": str(run_id)}
    invocation_started = time.monotonic()
    invocation_batches = 0
    try:
        while True:
            catalog_valid = _catalog_integrity_valid(db)
            run = db.scalar(
                select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
            )
            if run is None or run.status != "running" or run.lease_token != lease_token:
                db.rollback()
                return {"status": "lease_lost", "run_id": str(run_id)}
            tick = datetime.now(timezone.utc)
            if run.cancel_requested:
                _finish_run(
                    db,
                    run,
                    status="cancelled",
                    stop_reason="cancel_requested",
                    now=tick,
                )
                db.commit()
                return _execution_result(run)
            if not catalog_valid:
                run.error_code = "lifecycle_catalog_incomplete"
                run.error_message = (
                    "Lifecycle policy catalog integrity validation failed; no further records were changed."
                )
                _finish_run(
                    db,
                    run,
                    status="failed",
                    stop_reason="invalid_catalog",
                    now=tick,
                )
                db.commit()
                return _execution_result(run)
            current_policy = db.scalar(
                select(LifecyclePolicy)
                .where(LifecyclePolicy.target_key == run.target_key)
                .with_for_update()
            )
            if (
                current_policy is None
                or not current_policy.enabled
                or current_policy.revision != run.policy_revision
            ):
                _finish_policy_fenced(db, run, now=tick)
                db.commit()
                return _execution_result(run)
            contract_error = _run_contract_error(run, policy=current_policy)
            if contract_error is not None:
                run.error_code = "invalid_run_contract"
                run.error_message = (
                    "Lifecycle run configuration failed integrity validation; no records were changed."
                )
                _finish_run(
                    db,
                    run,
                    status="failed",
                    stop_reason="invalid_run_contract",
                    now=tick,
                )
                details = dict(run.details_json or {})
                details["contract_error"] = contract_error
                run.details_json = details
                db.commit()
                return _execution_result(run)
            remaining_budget = max(0, run.max_records - run.affected_count)
            if remaining_budget <= 0:
                _finish_after_preview(db, run, now=tick, stop_reason="record_limit")
                db.commit()
                return _execution_result(run)
            batch = execute_lifecycle_target_batch(
                db,
                target_key=run.target_key,
                cutoff=_utc(run.cutoff_at),
                batch_size=min(EXECUTION_BATCH_SIZE, remaining_budget),
                run_id=run.id,
                options=dict((run.policy_snapshot_json or {}).get("options") or {}),
                now=tick,
            )
            if batch.affected_count > remaining_budget:
                raise RuntimeError("Lifecycle target exceeded its durable record budget.")
            run.evaluated_count += batch.evaluated_count
            run.affected_count += batch.affected_count
            run.protected_count = max(run.protected_count, batch.protected_count)
            run.skipped_count += batch.skipped_count
            run.batch_count += 1
            invocation_batches += 1
            if batch.affected_bytes is not None:
                run.affected_bytes = int(run.affected_bytes or 0) + batch.affected_bytes
            run.details_json = _merge_details(run.details_json, batch.details)
            run.heartbeat_at = tick
            run.lease_expires_at = tick + RUN_LEASE_TTL
            if batch.affected_count == 0:
                details = dict(run.details_json or {})
                details["no_progress_batch_count"] = (
                    int(details.get("no_progress_batch_count") or 0) + 1
                )
                run.details_json = details
                remaining_preview = preview_lifecycle_target(
                    db,
                    target_key=run.target_key,
                    cutoff=_utc(run.cutoff_at),
                    options=dict(
                        (run.policy_snapshot_json or {}).get("options") or {}
                    ),
                    now=tick,
                )
                if (
                    remaining_preview.eligible_count > 0
                    and details["no_progress_batch_count"]
                    <= MAX_NO_PROGRESS_RETRIES
                ):
                    _apply_remaining_preview(run, remaining_preview)
                    _queue_continuation(db, run, now=tick)
                else:
                    _finish_after_preview(
                        db,
                        run,
                        now=tick,
                        stop_reason="candidates_locked_or_protected",
                        preview=remaining_preview,
                    )
                db.commit()
                return _execution_result(run)
            if run.affected_count >= run.max_records:
                _finish_after_preview(db, run, now=tick, stop_reason="record_limit")
                db.commit()
                return _execution_result(run)
            if (
                invocation_batches >= MAX_BATCHES_PER_INVOCATION
                or time.monotonic() - invocation_started >= MAX_INVOCATION_SECONDS
            ):
                _queue_continuation(db, run, now=tick)
                db.commit()
                return _execution_result(run)
            db.add(run)
            db.commit()
    except OperationalError as exc:
        logger.warning(
            "lifecycle_run_transient_failure run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        db.rollback()
        _release_run_for_retry(
            db,
            run_id=run_id,
            lease_token=lease_token,
            error_code=type(exc).__name__,
        )
        raise
    except Exception as exc:
        logger.exception("lifecycle_run_failed run_id=%s", run_id)
        db.rollback()
        _mark_run_failed(db, run_id=run_id, lease_token=lease_token, exc=exc)
        raise


def fail_lifecycle_run_after_retries(
    db: Session,
    *,
    run_id: uuid.UUID,
    expected_task_id: str,
) -> None:
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None or run.status != "queued" or run.celery_task_id != expected_task_id:
        db.rollback()
        return
    if run.cancel_requested:
        _finish_run(
            db,
            run,
            status="cancelled",
            stop_reason="cancel_requested",
            now=datetime.now(timezone.utc),
        )
    else:
        run.error_code = "transient_retries_exhausted"
        run.error_message = (
            "Lifecycle execution exhausted transient retries; inspect worker logs by run ID."
        )
        _finish_run(
            db,
            run,
            status="failed",
            stop_reason="retry_limit",
            now=datetime.now(timezone.utc),
        )
    db.commit()


def run_lifecycle_housekeeping(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    current_time = _utc(now)
    lifecycle_preview_ids = list(
        db.scalars(
            select(LifecyclePreview.id)
            .where(LifecyclePreview.expires_at <= current_time)
            .order_by(LifecyclePreview.expires_at, LifecyclePreview.id)
            .limit(HOUSEKEEPING_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        ).all()
    )
    expired_lifecycle_previews = _delete_ids(
        db, LifecyclePreview, lifecycle_preview_ids
    )
    alert_preview_ids = list(
        db.scalars(
            select(AlertBackfillPreview.id)
            .where(AlertBackfillPreview.expires_at <= current_time)
            .order_by(AlertBackfillPreview.expires_at, AlertBackfillPreview.id)
            .limit(HOUSEKEEPING_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        ).all()
    )
    expired_alert_previews = _delete_ids(db, AlertBackfillPreview, alert_preview_ids)
    mfa_challenges = cleanup_mfa_challenges(
        db,
        now=current_time,
        limit=HOUSEKEEPING_BATCH_SIZE,
    )
    pending_mfa = cleanup_pending_totp_enrollments(
        db,
        now=current_time,
        limit=HOUSEKEEPING_BATCH_SIZE,
    )
    orphan_result = prune_orphan_data_access_envelopes(
        db,
        limit=HOUSEKEEPING_BATCH_SIZE,
        max_dependent_rows=MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    )
    db.commit()
    integration_rollups = rollup_terminal_integration_deliveries(
        db,
        now=current_time,
        batch_size=HOUSEKEEPING_BATCH_SIZE,
    )
    alert_rollup = maintain_alert_history(
        db,
        now=current_time,
        batch_size=HOUSEKEEPING_BATCH_SIZE,
        occurrence_retention_days=1,
        max_batches=10,
        max_runtime_seconds=10.0,
        prune_expired_previews=False,
        aggregate_occurrences=True,
        prune_occurrences=False,
        prune_activities=False,
        prune_evaluations=False,
        prune_metrics=False,
    )
    return {
        "lifecycle_previews_deleted": expired_lifecycle_previews,
        "alert_previews_deleted": expired_alert_previews,
        "mfa_challenges_deleted": mfa_challenges,
        "pending_mfa_enrollments_deleted": pending_mfa,
        "orphan_data_access_envelopes_deleted": orphan_result.deleted_count,
        "orphan_backlog_remaining": orphan_result.backlog_remaining,
        "integration_deliveries_rolled_up": integration_rollups,
        "alert_occurrences_rolled_up": alert_rollup.occurrences_aggregated,
    }


def _claim_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    lease_token: str,
    expected_task_id: str | None,
    now: datetime,
) -> LifecycleRun | None:
    catalog_valid = _catalog_integrity_valid(db)
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None or run.status not in {"queued", "running"}:
        db.rollback()
        return None
    if expected_task_id is not None and run.celery_task_id != expected_task_id:
        db.rollback()
        return None
    if run.cancel_requested:
        _finish_run(
            db,
            run,
            status="cancelled",
            stop_reason="cancelled_before_start",
            now=now,
        )
        db.commit()
        return None
    if not catalog_valid:
        run.error_code = "lifecycle_catalog_incomplete"
        run.error_message = (
            "Lifecycle policy catalog integrity validation failed; no records were changed."
        )
        _finish_run(
            db,
            run,
            status="failed",
            stop_reason="invalid_catalog",
            now=now,
        )
        db.commit()
        return None
    if (
        run.status == "running"
        and run.lease_expires_at is not None
        and _utc(run.lease_expires_at) > now
    ):
        db.rollback()
        return None
    run.status = "running"
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    run.lease_token = lease_token
    run.lease_expires_at = now + RUN_LEASE_TTL
    run.error_code = None
    run.error_message = None
    policy = db.get(LifecyclePolicy, run.target_key)
    if policy is not None:
        policy.last_run_status = "running"
        policy.last_run_at = now
        db.add(policy)
    db.add(run)
    db.commit()
    return run


def _finish_after_preview(
    db: Session,
    run: LifecycleRun,
    *,
    now: datetime,
    stop_reason: str,
    preview=None,
) -> None:
    if preview is None:
        preview = preview_lifecycle_target(
            db,
            target_key=run.target_key,
            cutoff=_utc(run.cutoff_at),
            options=dict((run.policy_snapshot_json or {}).get("options") or {}),
            now=now,
        )
    _apply_remaining_preview(run, preview)
    blocked_by_dependency_limit = bool(
        int(preview.protected_counts.get("dependent_row_limit") or 0)
    )
    status = (
        "partial"
        if preview.eligible_count > 0
        or (
            stop_reason == "candidates_locked_or_protected"
            and blocked_by_dependency_limit
        )
        else "succeeded"
    )
    effective_reason = "drained" if status == "succeeded" else stop_reason
    _finish_run(db, run, status=status, stop_reason=effective_reason, now=now)


def _apply_remaining_preview(run: LifecycleRun, preview) -> None:
    run.remaining_count = preview.eligible_count
    run.protected_count = max(run.protected_count, preview.protected_count)
    details = dict(run.details_json or {})
    details["remaining_count_is_lower_bound"] = preview.count_is_lower_bound
    details["protected_counts"] = preview.protected_counts
    run.details_json = details


def _finish_policy_fenced(db: Session, run: LifecycleRun, *, now: datetime) -> None:
    preview = preview_lifecycle_target(
        db,
        target_key=run.target_key,
        cutoff=_utc(run.cutoff_at),
        options=dict((run.policy_snapshot_json or {}).get("options") or {}),
        now=now,
    )
    run.remaining_count = preview.eligible_count
    run.protected_count = max(run.protected_count, preview.protected_count)
    details = dict(run.details_json or {})
    details["remaining_count_is_lower_bound"] = preview.count_is_lower_bound
    run.details_json = details
    _finish_run(
        db,
        run,
        status="partial" if run.affected_count else "cancelled",
        stop_reason="policy_changed",
        now=now,
    )


def _queue_continuation(db: Session, run: LifecycleRun, *, now: datetime) -> None:
    details = dict(run.details_json or {})
    details["continuation_count"] = int(details.get("continuation_count") or 0) + 1
    run.details_json = details
    run.status = "queued"
    run.heartbeat_at = now
    run.lease_token = None
    run.lease_expires_at = None
    run.celery_task_id = None
    policy = db.get(LifecyclePolicy, run.target_key)
    if policy is not None:
        policy.last_run_status = "queued"
        policy.last_run_at = now
        db.add(policy)
    db.add(run)


def _finish_run(
    db: Session,
    run: LifecycleRun,
    *,
    status: str,
    stop_reason: str,
    now: datetime,
) -> None:
    run.status = status
    run.stop_reason = stop_reason
    run.finished_at = now
    run.heartbeat_at = now
    run.lease_token = None
    run.lease_expires_at = None
    db.add(run)
    policy = db.get(LifecyclePolicy, run.target_key)
    if policy is not None:
        policy.last_run_status = status
        policy.last_run_at = now
        db.add(policy)
    record_audit(
        db,
        actor_user_id=run.requested_by_user_id,
        actor_principal_type=(
            "user" if run.requested_by_user_id_snapshot is not None else "system"
        ),
        actor_principal_id=run.requested_by_user_id_snapshot,
        actor_label_snapshot=run.requested_by_label_snapshot,
        action="lifecycle.run.complete",
        resource_type="lifecycle_run",
        resource_id=str(run.id),
        resource_label_snapshot=target_definition(run.target_key).label,
        success=status in {"succeeded", "partial", "cancelled"},
        metadata={
            "target_key": run.target_key,
            "status": status,
            "stop_reason": stop_reason,
            "policy_revision": run.policy_revision,
            "cutoff_at": _utc(run.cutoff_at).isoformat(),
            "affected_count": run.affected_count,
            "protected_count": run.protected_count,
            "skipped_count": run.skipped_count,
            "remaining_count": run.remaining_count,
            "batch_count": run.batch_count,
            "affected_bytes": run.affected_bytes,
            "requested_by": run.requested_by_label_snapshot,
        },
    )


def _mark_run_failed(
    db: Session,
    *,
    run_id: uuid.UUID,
    lease_token: str,
    exc: Exception,
) -> None:
    now = datetime.now(timezone.utc)
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None or run.status != "running" or run.lease_token != lease_token:
        db.rollback()
        return
    if run.cancel_requested:
        _finish_run(
            db,
            run,
            status="cancelled",
            stop_reason="cancel_requested",
            now=now,
        )
    else:
        run.error_code = type(exc).__name__[:64]
        run.error_message = (
            "Lifecycle target execution failed; inspect worker logs by run ID."
        )
        _finish_run(db, run, status="failed", stop_reason="execution_error", now=now)
    db.commit()


def _release_run_for_retry(
    db: Session,
    *,
    run_id: uuid.UUID,
    lease_token: str,
    error_code: str,
) -> None:
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None or run.status != "running" or run.lease_token != lease_token:
        db.rollback()
        return
    details = dict(run.details_json or {})
    details["transient_retry_count"] = (
        int(details.get("transient_retry_count") or 0) + 1
    )
    details["last_transient_error_code"] = error_code[:64]
    run.details_json = details
    run.status = "queued"
    run.lease_token = None
    run.lease_expires_at = None
    policy = db.get(LifecyclePolicy, run.target_key)
    if policy is not None:
        policy.last_run_status = "queued"
        db.add(policy)
    db.add(run)
    db.commit()


def _recover_stale_runs(db: Session, *, now: datetime) -> None:
    stale = list(
        db.scalars(
            select(LifecycleRun)
            .where(
                LifecycleRun.status == "running",
                LifecycleRun.lease_expires_at.is_not(None),
                LifecycleRun.lease_expires_at <= now,
            )
            .order_by(LifecycleRun.lease_expires_at, LifecycleRun.id)
            .limit(DISPATCH_LIMIT)
            .with_for_update(skip_locked=True)
        ).all()
    )
    for run in stale:
        if run.cancel_requested:
            _finish_run(
                db,
                run,
                status="cancelled",
                stop_reason="cancel_requested",
                now=now,
            )
        else:
            details = dict(run.details_json or {})
            details["stale_lease_recovery_count"] = (
                int(details.get("stale_lease_recovery_count") or 0) + 1
            )
            run.details_json = details
            run.status = "queued"
            run.lease_token = None
            run.lease_expires_at = None
            run.celery_task_id = None
            run.error_code = "stale_lease_recovered"
            run.error_message = None
            policy = db.get(LifecyclePolicy, run.target_key)
            if policy is not None:
                policy.last_run_status = "queued"
                policy.last_run_at = now
                db.add(policy)
            db.add(run)


def _fence_active_runs_for_invalid_catalog(
    db: Session,
    *,
    now: datetime,
    failure_code: str,
) -> None:
    active_runs = list(
        db.scalars(
            select(LifecycleRun)
            .where(LifecycleRun.status.in_(("queued", "running")))
            .order_by(LifecycleRun.queued_at, LifecycleRun.id)
            .with_for_update()
        ).all()
    )
    for run in active_runs:
        details = dict(run.details_json or {})
        details["catalog_integrity_failure"] = failure_code
        run.details_json = details
        if run.cancel_requested:
            _finish_run(
                db,
                run,
                status="cancelled",
                stop_reason="cancel_requested",
                now=now,
            )
            continue
        run.error_code = "invalid_catalog"
        run.error_message = (
            "Lifecycle policy catalog integrity validation failed; no further records were changed."
        )
        _finish_run(
            db,
            run,
            status="failed",
            stop_reason="invalid_catalog",
            now=now,
        )


def _catalog_integrity_valid(db: Session) -> bool:
    state = db.get(LifecycleCatalogState, 1)
    if state is None or state.bootstrapped_at is None:
        return False
    actual_keys = set(db.scalars(select(LifecyclePolicy.target_key)).all())
    return actual_keys == {target.key for target in TARGETS}


def _policy_snapshot(policy: LifecyclePolicy) -> dict:
    definition = target_definition(policy.target_key)
    known_options = {
        option.key: option.default for option in definition.available_options
    }
    known_options.update(
        {
            key: bool(value)
            for key, value in (policy.options_json or {}).items()
            if key in known_options
        }
    )
    return {
        "target_key": policy.target_key,
        "enabled": policy.enabled,
        "retention_days": policy.retention_days,
        "schedule_cadence": policy.schedule_cadence,
        "schedule_hour_utc": policy.schedule_hour_utc,
        "schedule_weekday": policy.schedule_weekday,
        "max_records_per_run": policy.max_records_per_run,
        "options": known_options,
    }


def _run_contract_error(
    run: LifecycleRun,
    *,
    policy: LifecyclePolicy,
) -> str | None:
    snapshot = dict(run.policy_snapshot_json or {})
    if snapshot != _policy_snapshot(policy):
        return "policy_snapshot_mismatch"
    if run.max_records != int(snapshot["max_records_per_run"]):
        return "record_limit_mismatch"
    retention = timedelta(days=int(snapshot["retention_days"]))
    cutoff = _utc(run.cutoff_at)
    if run.trigger_source == "scheduled":
        if run.scheduled_for is None:
            return "scheduled_timestamp_missing"
        if cutoff != _utc(run.scheduled_for) - retention:
            return "scheduled_cutoff_mismatch"
    elif run.trigger_source == "manual":
        latest_safe_cutoff = _utc(run.queued_at) - retention
        if cutoff > latest_safe_cutoff:
            return "manual_cutoff_too_recent"
    else:
        return "trigger_source_invalid"
    return None


def _merge_details(current: dict | None, increment: dict) -> dict:
    merged = dict(current or {})
    for key, value in increment.items():
        if isinstance(value, int) and isinstance(merged.get(key), int):
            merged[key] += value
        else:
            merged[key] = value
    return merged


def _delete_ids(db: Session, model, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    result = db.execute(
        delete(model)
        .where(model.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _execution_result(run: LifecycleRun) -> dict:
    return {
        "status": run.status,
        "run_id": str(run.id),
        "affected_count": int(run.affected_count),
        "remaining_count": run.remaining_count,
        "stop_reason": run.stop_reason,
    }


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


__all__ = [
    "dispatch_due_lifecycle_runs",
    "execute_lifecycle_run",
    "fail_lifecycle_run_after_retries",
    "mark_lifecycle_run_published",
    "release_lifecycle_run_publication",
    "run_lifecycle_housekeeping",
]
