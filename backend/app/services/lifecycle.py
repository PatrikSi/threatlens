from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.lifecycle import (
    LifecycleCatalogState,
    LifecyclePolicy,
    LifecyclePreview,
    LifecycleRun,
)
from app.models.user import User
from app.schemas.lifecycle import (
    LifecycleOverviewResponse,
    LifecyclePolicyDraft,
    LifecyclePolicyResponse,
    LifecyclePolicyUpdateRequest,
    LifecyclePreviewResponse,
    LifecycleRunListResponse,
    LifecycleRunResponse,
    LifecycleSafeguardResponse,
    LifecycleTargetResponse,
)
from app.services.lifecycle_catalog import (
    TARGETS,
    next_scheduled_at,
    normalized_options,
    target_definition,
)
from app.services.lifecycle_targets import preview_lifecycle_target
from app.services.audit import record_audit


PREVIEW_TTL = timedelta(minutes=15)
IDEMPOTENCY_KEY_MIN_LENGTH = 8
IDEMPOTENCY_KEY_MAX_LENGTH = 255
LIFECYCLE_BOOTSTRAP_AUDIT_ACTION = "lifecycle.policy.bootstrap"


class LifecycleError(Exception):
    error_code = "lifecycle_error"


class LifecycleNotFound(LifecycleError):
    error_code = "lifecycle_not_found"


class LifecycleConflict(LifecycleError):
    error_code = "lifecycle_conflict"


class LifecycleRevisionConflict(LifecycleConflict):
    error_code = "lifecycle_revision_conflict"


class LifecyclePreviewConflict(LifecycleConflict):
    error_code = "lifecycle_preview_conflict"


class LifecycleValidationError(LifecycleError):
    error_code = "lifecycle_invalid"


def ensure_lifecycle_policies(
    db: Session,
    *,
    now: datetime | None = None,
    commit_missing: bool = False,
) -> list[LifecyclePolicy]:
    current_time = _utc(now)
    expected_keys = {target.key for target in TARGETS}
    catalog_state = db.get(LifecycleCatalogState, 1)
    if catalog_state is None:
        raise LifecycleConflict(
            "The lifecycle policy catalog marker is missing; repair it before cleanup can continue."
        )
    existing = list(
        db.scalars(select(LifecyclePolicy).order_by(LifecyclePolicy.target_key)).all()
    )
    if catalog_state.bootstrapped_at is not None:
        if {policy.target_key for policy in existing} != expected_keys:
            raise LifecycleConflict(
                "The lifecycle policy catalog is incomplete; repair it before cleanup can continue."
            )
        return existing

    catalog_state = db.scalar(
        select(LifecycleCatalogState)
        .where(LifecycleCatalogState.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if catalog_state is None:
        raise LifecycleConflict(
            "The lifecycle policy catalog marker is missing; repair it before cleanup can continue."
        )
    existing = list(
        db.scalars(select(LifecyclePolicy).order_by(LifecyclePolicy.target_key)).all()
    )
    if catalog_state.bootstrapped_at is not None:
        if {policy.target_key for policy in existing} != expected_keys:
            raise LifecycleConflict(
                "The lifecycle policy catalog is incomplete; repair it before cleanup can continue."
            )
        return existing
    if existing:
        raise LifecycleConflict(
            "The lifecycle policy catalog contains unmarked data; repair it before cleanup can continue."
        )
    rows = []
    for index, target in enumerate(TARGETS):
        enabled = target.enabled_by_default
        default_hour = (2 + index) % 24
        rows.append(
            {
                "target_key": target.key,
                "enabled": enabled,
                "retention_days": target.default_retention_days(),
                "schedule_cadence": "daily",
                "schedule_hour_utc": default_hour,
                "schedule_weekday": None,
                "max_records_per_run": 10_000,
                "options_json": target.default_options(),
                "revision": 1,
                "next_run_at": next_scheduled_at(
                    cadence="daily",
                    hour_utc=default_hour,
                    weekday=None,
                    after=current_time,
                )
                if enabled
                else None,
            }
        )
    inserted_keys = set(
        db.scalars(
            pg_insert(LifecyclePolicy)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[LifecyclePolicy.target_key])
            .returning(LifecyclePolicy.target_key)
        ).all()
    )
    db.flush()
    if inserted_keys != expected_keys:
        raise LifecycleConflict(
            "The lifecycle policy catalog could not be initialized atomically."
        )
    snapshot = {
        "catalog_version": int(catalog_state.catalog_version),
        "targets": [
            {
                **{key: value for key, value in row.items() if key != "next_run_at"},
                "next_run_at": (
                    _utc(row["next_run_at"]).isoformat()
                    if row["next_run_at"] is not None
                    else None
                ),
            }
            for row in rows
        ],
    }
    catalog_state.bootstrapped_at = current_time
    catalog_state.bootstrap_snapshot_json = snapshot
    db.add(catalog_state)
    record_audit(
        db,
        actor_user_id=None,
        actor_principal_type="system",
        action=LIFECYCLE_BOOTSTRAP_AUDIT_ACTION,
        resource_type="lifecycle_policy_catalog",
        resource_id="fixed-target-registry",
        resource_label_snapshot="Lifecycle policy catalog",
        metadata={
            "target_count": len(rows),
            "defaults_source": "runtime_environment",
            "bootstrap_snapshot": snapshot,
        },
    )
    db.flush()
    if commit_missing:
        db.commit()
    policies = list(
        db.scalars(select(LifecyclePolicy).order_by(LifecyclePolicy.target_key)).all()
    )
    if {policy.target_key for policy in policies} != expected_keys:
        raise LifecycleConflict(
            "The lifecycle policy catalog is incomplete; repair it before cleanup can continue."
        )
    return policies


def lifecycle_overview(
    db: Session,
    *,
    requested_by_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> LifecycleOverviewResponse:
    current_time = _utc(now)
    policies = {
        row.target_key: row
        for row in ensure_lifecycle_policies(
            db,
            now=current_time,
            commit_missing=True,
        )
    }
    user_labels = _user_labels(
        db,
        {row.updated_by_user_id for row in policies.values() if row.updated_by_user_id},
    )
    latest_previews = _latest_current_previews(
        db,
        policies=policies,
        requested_by_user_id=requested_by_user_id,
        now=current_time,
    )
    targets = []
    for definition in TARGETS:
        policy = policies[definition.key]
        targets.append(
            LifecycleTargetResponse(
                key=definition.key,
                label=definition.label,
                category=definition.category,
                description=definition.description,
                action_description=definition.action_description,
                cutoff_description=definition.cutoff_description,
                min_retention_days=definition.min_retention_days,
                max_retention_days=definition.max_retention_days,
                default_retention_days=definition.default_retention_days(),
                safeguards=[
                    LifecycleSafeguardResponse(
                        key=option.key,
                        label=option.label,
                        description=option.description,
                        default_enabled=option.default,
                    )
                    for option in definition.available_options
                ],
                policy=_policy_response(
                    policy,
                    updated_by=user_labels.get(policy.updated_by_user_id),
                ),
                latest_preview=(
                    _preview_response(
                        latest_previews[definition.key],
                        observed_at=current_time,
                    )
                    if definition.key in latest_previews
                    else None
                ),
            )
        )
    return LifecycleOverviewResponse(generated_at=current_time, targets=targets)


def update_lifecycle_policy(
    db: Session,
    *,
    target_key: str,
    payload: LifecyclePolicyUpdateRequest,
    actor: User,
    now: datetime | None = None,
) -> LifecyclePolicyResponse:
    current_time = _utc(now)
    definition = _definition(target_key)
    policy = _locked_policy(db, target_key=target_key, now=current_time)
    if policy.revision != payload.expected_revision:
        raise LifecycleRevisionConflict(
            "The lifecycle policy changed; reload before saving."
        )
    _validate_policy_fields(definition, payload)
    new_options = normalized_options(definition, payload.options)
    previous_options = normalized_options(definition, policy.options_json)
    if (
        policy.enabled == payload.enabled
        and policy.retention_days == payload.retention_days
        and policy.schedule_cadence == payload.schedule_cadence
        and policy.schedule_hour_utc == payload.schedule_hour_utc
        and policy.schedule_weekday == payload.schedule_weekday
        and policy.max_records_per_run == payload.max_records_per_run
        and previous_options == new_options
    ):
        raise LifecycleValidationError(
            "No lifecycle policy changes were provided."
        )
    safeguard_reduced = definition.key == "article_content" and any(
        previous_options.get(key, False) and not new_options.get(key, False)
        for key in previous_options
    )
    destructive = (
        (payload.enabled and not policy.enabled)
        or payload.retention_days < policy.retention_days
        or (
            policy.enabled
            and payload.enabled
            and payload.max_records_per_run > policy.max_records_per_run
        )
        or safeguard_reduced
    )
    if destructive and (payload.confirmation != "PURGE" or not payload.reason):
        raise LifecycleValidationError(
            "Expanding cleanup scope requires PURGE confirmation and a reason."
        )
    if destructive:
        _consume_destructive_policy_preview(
            db,
            policy=policy,
            definition=definition,
            payload=payload,
            actor=actor,
            now=current_time,
        )
    policy.enabled = payload.enabled
    policy.retention_days = payload.retention_days
    policy.schedule_cadence = payload.schedule_cadence
    policy.schedule_hour_utc = payload.schedule_hour_utc
    policy.schedule_weekday = payload.schedule_weekday
    policy.max_records_per_run = payload.max_records_per_run
    policy.options_json = new_options
    policy.revision += 1
    policy.updated_by_user_id = actor.id
    policy.updated_by_label_snapshot = actor.email
    policy.configuration_updated_at = current_time
    policy.next_run_at = (
        next_scheduled_at(
            cadence=policy.schedule_cadence,
            hour_utc=policy.schedule_hour_utc,
            weekday=policy.schedule_weekday,
            after=current_time,
        )
        if policy.enabled
        else None
    )
    db.add(policy)
    db.flush()
    db.refresh(policy)
    return _policy_response(policy, updated_by=actor.email)


def create_lifecycle_preview(
    db: Session,
    *,
    target_key: str,
    expected_revision: int,
    draft: LifecyclePolicyDraft,
    requested_by_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> LifecyclePreviewResponse:
    current_time = _utc(now)
    definition = _definition(target_key)
    policy = _locked_policy(db, target_key=target_key, now=current_time)
    if policy.revision != expected_revision:
        raise LifecycleRevisionConflict(
            "The lifecycle policy changed; refresh the preview."
        )
    _validate_policy_fields(definition, draft)
    snapshot = _draft_snapshot(target_key, draft, definition=definition)
    request_fingerprint = _fingerprint(snapshot)
    reusable = db.scalar(
        select(LifecyclePreview)
        .where(
            LifecyclePreview.target_key == target_key,
            LifecyclePreview.policy_revision == policy.revision,
            LifecyclePreview.requested_by_user_id == requested_by_user_id,
            LifecyclePreview.request_fingerprint == request_fingerprint,
            LifecyclePreview.used_at.is_(None),
            LifecyclePreview.expires_at > current_time,
        )
        .order_by(LifecyclePreview.generated_at.desc(), LifecyclePreview.id.desc())
        .limit(1)
        .with_for_update()
    )
    if reusable is not None:
        return _preview_response(reusable, observed_at=current_time)
    cutoff = current_time - timedelta(days=draft.retention_days)
    result = preview_lifecycle_target(
        db,
        target_key=target_key,
        cutoff=cutoff,
        options=snapshot["options"],
        now=current_time,
    )
    db.execute(
        delete(LifecyclePreview).where(
            LifecyclePreview.target_key == target_key,
            LifecyclePreview.requested_by_user_id == requested_by_user_id,
            LifecyclePreview.used_at.is_(None),
        )
    )
    preview = LifecyclePreview(
        target_key=target_key,
        policy_revision=policy.revision,
        policy_snapshot_json=snapshot,
        request_fingerprint=request_fingerprint,
        cutoff_at=cutoff,
        eligible_count=result.eligible_count,
        protected_count=result.protected_count,
        protected_counts_json=result.protected_counts,
        oldest_candidate_at=result.oldest_candidate_at,
        count_is_lower_bound=result.count_is_lower_bound,
        is_partial=result.is_partial,
        eligible_bytes=result.eligible_bytes,
        requested_by_user_id=requested_by_user_id,
        generated_at=current_time,
        expires_at=current_time + PREVIEW_TTL,
    )
    db.add(preview)
    db.flush()
    return _preview_response(preview, observed_at=current_time)


def create_manual_lifecycle_run(
    db: Session,
    *,
    target_key: str,
    expected_revision: int,
    preview_id: uuid.UUID,
    reason: str,
    idempotency_key: str,
    actor: User,
    now: datetime | None = None,
) -> tuple[LifecycleRunResponse, bool]:
    current_time = _utc(now)
    _definition(target_key)
    key_hash = _idempotency_hash(idempotency_key)
    request_fingerprint = _fingerprint(
        {
            "target_key": target_key,
            "expected_revision": expected_revision,
            "preview_id": str(preview_id),
            "reason": reason,
        }
    )
    policy = _locked_policy(db, target_key=target_key, now=current_time)
    replay = db.scalar(
        select(LifecycleRun).where(
            LifecycleRun.requested_by_user_id == actor.id,
            LifecycleRun.target_key == target_key,
            LifecycleRun.idempotency_key_hash == key_hash,
        )
    )
    if replay is not None:
        if replay.request_fingerprint != request_fingerprint:
            raise LifecycleConflict("The idempotency key was used for another request.")
        return _run_response(replay, requested_by=actor.email), True
    if policy.revision != expected_revision:
        raise LifecycleRevisionConflict(
            "The lifecycle policy changed; create a new preview."
        )
    if not policy.enabled:
        raise LifecycleConflict("Enable the lifecycle policy before running it.")
    preview = db.scalar(
        select(LifecyclePreview)
        .where(LifecyclePreview.id == preview_id)
        .with_for_update()
    )
    if preview is None or preview.target_key != target_key:
        raise LifecycleNotFound("Lifecycle preview not found.")
    if preview.requested_by_user_id != actor.id:
        raise LifecycleNotFound("Lifecycle preview not found.")
    if preview.used_at is not None:
        raise LifecyclePreviewConflict(
            "This lifecycle preview has already been used."
        )
    if _utc(preview.expires_at) <= current_time:
        raise LifecyclePreviewConflict(
            "This lifecycle preview expired; create a new preview."
        )
    if preview.policy_revision != policy.revision:
        raise LifecycleRevisionConflict(
            "The lifecycle policy changed; create a new preview."
        )
    snapshot = _policy_snapshot(policy)
    if preview.request_fingerprint != _fingerprint(snapshot):
        raise LifecyclePreviewConflict(
            "Save this policy draft before starting cleanup."
        )
    active = db.scalar(
        select(LifecycleRun.id).where(
            LifecycleRun.target_key == target_key,
            LifecycleRun.status.in_(("queued", "running")),
        )
    )
    if active is not None:
        raise LifecycleConflict("A lifecycle run for this dataset is already active.")
    run = LifecycleRun(
        target_key=target_key,
        trigger_source="manual",
        status="queued",
        policy_revision=policy.revision,
        policy_snapshot_json=snapshot,
        preview_id=preview.id,
        preview_id_snapshot=preview.id,
        requested_by_user_id=actor.id,
        requested_by_user_id_snapshot=actor.id,
        requested_by_label_snapshot=actor.email,
        reason=reason,
        idempotency_key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        cutoff_at=preview.cutoff_at,
        scheduled_for=None,
        max_records=policy.max_records_per_run,
        protected_count=preview.protected_count,
        details_json={"preview": _preview_evidence(preview)},
        queued_at=current_time,
    )
    preview.used_at = current_time
    policy.last_run_at = current_time
    policy.last_run_status = "queued"
    db.add_all((preview, run, policy))
    db.flush()
    return _run_response(run, requested_by=actor.email), False


def cancel_lifecycle_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    reason: str,
    actor: User,
    now: datetime | None = None,
) -> LifecycleRunResponse:
    current_time = _utc(now)
    run = db.scalar(
        select(LifecycleRun).where(LifecycleRun.id == run_id).with_for_update()
    )
    if run is None:
        raise LifecycleNotFound("Lifecycle run not found.")
    if run.status not in {"queued", "running"}:
        raise LifecycleConflict(
            "Only queued or running lifecycle runs can be cancelled."
        )
    run.cancel_requested = True
    run.cancel_requested_at = current_time
    run.cancel_requested_by_user_id = actor.id
    run.cancel_requested_by_principal_type = "user"
    run.cancel_requested_by_user_id_snapshot = actor.id
    run.cancel_requested_by_label_snapshot = actor.email
    run.cancellation_reason = reason
    details = dict(run.details_json or {})
    details["cancellation_reason"] = reason
    run.details_json = details
    if run.status == "queued":
        run.status = "cancelled"
        run.stop_reason = "cancelled_before_start"
        run.finished_at = current_time
        _set_policy_run_status(db, run, current_time=current_time)
    db.add(run)
    db.flush()
    return _run_response(run, requested_by=_user_label(db, run.requested_by_user_id))


def get_lifecycle_run(db: Session, run_id: uuid.UUID) -> LifecycleRunResponse:
    run = db.get(LifecycleRun, run_id)
    if run is None:
        raise LifecycleNotFound("Lifecycle run not found.")
    return _run_response(run, requested_by=_user_label(db, run.requested_by_user_id))


def list_lifecycle_runs(
    db: Session,
    *,
    page: int,
    page_size: int,
    target_key: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
) -> LifecycleRunListResponse:
    filters = []
    if target_key is not None:
        _definition(target_key)
        filters.append(LifecycleRun.target_key == target_key)
    if status is not None:
        filters.append(LifecycleRun.status == status)
    if trigger_source is not None:
        filters.append(LifecycleRun.trigger_source == trigger_source)
    total = int(db.scalar(select(func.count(LifecycleRun.id)).where(*filters)) or 0)
    runs = list(
        db.scalars(
            select(LifecycleRun)
            .where(*filters)
            .order_by(LifecycleRun.created_at.desc(), LifecycleRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    labels = _user_labels(
        db,
        {run.requested_by_user_id for run in runs if run.requested_by_user_id},
    )
    return LifecycleRunListResponse(
        runs=[
            _run_response(run, requested_by=labels.get(run.requested_by_user_id))
            for run in runs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _validate_policy_fields(definition, draft: LifecyclePolicyDraft) -> None:
    if not (
        definition.min_retention_days
        <= draft.retention_days
        <= definition.max_retention_days
    ):
        raise LifecycleValidationError(
            f"Retention for {definition.label} must be between "
            f"{definition.min_retention_days} and {definition.max_retention_days} days."
        )
    try:
        normalized_options(definition, draft.options)
    except ValueError as exc:
        raise LifecycleValidationError(str(exc)) from exc


def _definition(target_key: str):
    try:
        return target_definition(target_key)
    except ValueError as exc:
        raise LifecycleNotFound("Lifecycle target not found.") from exc


def _locked_policy(
    db: Session,
    *,
    target_key: str,
    now: datetime,
) -> LifecyclePolicy:
    ensure_lifecycle_policies(db, now=now)
    policy = db.scalar(
        select(LifecyclePolicy)
        .where(LifecyclePolicy.target_key == target_key)
        .with_for_update()
    )
    if policy is None:
        raise LifecycleNotFound("Lifecycle policy not found.")
    return policy


def _policy_snapshot(policy: LifecyclePolicy) -> dict:
    definition = _definition(policy.target_key)
    return {
        "target_key": policy.target_key,
        "enabled": bool(policy.enabled),
        "retention_days": int(policy.retention_days),
        "schedule_cadence": policy.schedule_cadence,
        "schedule_hour_utc": int(policy.schedule_hour_utc),
        "schedule_weekday": policy.schedule_weekday,
        "max_records_per_run": int(policy.max_records_per_run),
        "options": normalized_options(definition, policy.options_json),
    }


def lifecycle_policy_audit_snapshot(
    db: Session,
    *,
    target_key: str,
    now: datetime | None = None,
) -> dict[str, object]:
    policy = _locked_policy(db, target_key=target_key, now=_utc(now))
    return _policy_audit_snapshot(policy)


def lifecycle_policy_response_audit_snapshot(
    policy: LifecyclePolicyResponse,
) -> dict[str, object]:
    return {
        "target_key": policy.target_key,
        "enabled": bool(policy.enabled),
        "retention_days": int(policy.retention_days),
        "schedule_cadence": policy.schedule_cadence,
        "schedule_hour_utc": int(policy.schedule_hour_utc),
        "schedule_weekday": policy.schedule_weekday,
        "max_records_per_run": int(policy.max_records_per_run),
        "options": dict(policy.options),
        "revision": int(policy.revision),
        "next_run_at": (
            _utc(policy.next_run_at).isoformat() if policy.next_run_at else None
        ),
        "configuration_updated_at": _utc(
            policy.configuration_updated_at
        ).isoformat(),
    }


def _policy_audit_snapshot(policy: LifecyclePolicy) -> dict[str, object]:
    return {
        **_policy_snapshot(policy),
        "revision": int(policy.revision),
        "next_run_at": (
            _utc(policy.next_run_at).isoformat() if policy.next_run_at else None
        ),
        "configuration_updated_at": _utc(
            policy.configuration_updated_at
        ).isoformat(),
    }


def _draft_snapshot(
    target_key: str, draft: LifecyclePolicyDraft, *, definition
) -> dict:
    return {
        "target_key": target_key,
        "enabled": bool(draft.enabled),
        "retention_days": int(draft.retention_days),
        "schedule_cadence": draft.schedule_cadence,
        "schedule_hour_utc": int(draft.schedule_hour_utc),
        "schedule_weekday": draft.schedule_weekday,
        "max_records_per_run": int(draft.max_records_per_run),
        "options": normalized_options(definition, draft.options),
    }


def _consume_destructive_policy_preview(
    db: Session,
    *,
    policy: LifecyclePolicy,
    definition,
    payload: LifecyclePolicyUpdateRequest,
    actor: User,
    now: datetime,
) -> None:
    preview_id = getattr(payload, "preview_id", None)
    if preview_id is None:
        raise LifecycleValidationError(
            "Create a fresh lifecycle preview before saving this destructive change."
        )
    preview = db.scalar(
        select(LifecyclePreview)
        .where(LifecyclePreview.id == preview_id)
        .with_for_update()
    )
    expected_fingerprint = _fingerprint(
        _draft_snapshot(policy.target_key, payload, definition=definition)
    )
    if (
        preview is None
        or preview.target_key != policy.target_key
        or preview.requested_by_user_id != actor.id
    ):
        raise LifecycleNotFound("Lifecycle preview not found.")
    if preview.used_at is not None:
        raise LifecyclePreviewConflict(
            "This lifecycle preview has already been used."
        )
    if _utc(preview.expires_at) <= now:
        raise LifecyclePreviewConflict(
            "This lifecycle preview expired; create a new preview."
        )
    if preview.policy_revision != policy.revision:
        raise LifecycleRevisionConflict(
            "The lifecycle policy changed; create a new preview."
        )
    if preview.request_fingerprint != expected_fingerprint:
        raise LifecyclePreviewConflict(
            "The lifecycle preview does not match this policy draft; create a new preview."
        )
    preview.used_at = now
    db.add(preview)


def _policy_response(
    policy: LifecyclePolicy,
    *,
    updated_by: str | None,
) -> LifecyclePolicyResponse:
    return LifecyclePolicyResponse(
        target_key=policy.target_key,
        enabled=policy.enabled,
        retention_days=policy.retention_days,
        schedule_cadence=policy.schedule_cadence,
        schedule_hour_utc=policy.schedule_hour_utc,
        schedule_weekday=policy.schedule_weekday,
        max_records_per_run=policy.max_records_per_run,
        options=dict(policy.options_json or {}),
        revision=policy.revision,
        next_run_at=policy.next_run_at,
        last_run_at=policy.last_run_at,
        last_run_status=policy.last_run_status,
        configuration_updated_at=policy.configuration_updated_at,
        updated_at=policy.updated_at,
        updated_by=policy.updated_by_label_snapshot or updated_by,
    )


def _preview_response(
    preview: LifecyclePreview,
    *,
    observed_at: datetime,
) -> LifecyclePreviewResponse:
    return LifecyclePreviewResponse(
        id=preview.id,
        target_key=preview.target_key,
        policy_revision=preview.policy_revision,
        cutoff_at=preview.cutoff_at,
        eligible_count=preview.eligible_count,
        protected_count=preview.protected_count,
        protected_counts=dict(preview.protected_counts_json or {}),
        oldest_candidate_at=preview.oldest_candidate_at,
        eligible_bytes=preview.eligible_bytes,
        count_is_lower_bound=preview.count_is_lower_bound,
        is_partial=preview.is_partial,
        generated_at=preview.generated_at,
        expires_at=preview.expires_at,
        observed_at=observed_at,
    )


def lifecycle_preview_evidence(
    db: Session,
    *,
    preview_id: uuid.UUID | None,
    consumed_for_policy_revision: int | None = None,
) -> dict[str, object] | None:
    if preview_id is None:
        return None
    preview = db.get(LifecyclePreview, preview_id)
    if preview is None:
        return None
    if consumed_for_policy_revision is not None and (
        preview.used_at is None
        or preview.policy_revision + 1 != consumed_for_policy_revision
    ):
        return None
    return _preview_evidence(preview)


def _preview_evidence(preview: LifecyclePreview) -> dict[str, object]:
    return {
        "preview_id": str(preview.id),
        "cutoff_at": _utc(preview.cutoff_at).isoformat(),
        "eligible_count": int(preview.eligible_count),
        "protected_count": int(preview.protected_count),
        "protected_counts": dict(preview.protected_counts_json or {}),
        "eligible_bytes": preview.eligible_bytes,
        "count_is_lower_bound": bool(preview.count_is_lower_bound),
        "is_partial": bool(preview.is_partial),
        "generated_at": _utc(preview.generated_at).isoformat(),
        "expires_at": _utc(preview.expires_at).isoformat(),
    }


def _run_response(
    run: LifecycleRun, *, requested_by: str | None
) -> LifecycleRunResponse:
    return LifecycleRunResponse(
        id=run.id,
        target_key=run.target_key,
        trigger_source=run.trigger_source,
        status=run.status,
        policy_revision=run.policy_revision,
        policy_snapshot=dict(run.policy_snapshot_json or {}),
        cutoff_at=run.cutoff_at,
        scheduled_for=run.scheduled_for,
        reason=run.reason,
        requested_by=run.requested_by_label_snapshot or requested_by,
        max_records=run.max_records,
        evaluated_count=run.evaluated_count,
        affected_count=run.affected_count,
        protected_count=run.protected_count,
        skipped_count=run.skipped_count,
        batch_count=run.batch_count,
        remaining_count=run.remaining_count,
        affected_bytes=run.affected_bytes,
        details=dict(run.details_json or {}),
        stop_reason=run.stop_reason,
        error_code=run.error_code,
        error_message=run.error_message,
        cancel_requested=run.cancel_requested,
        cancellation_requested_at=run.cancel_requested_at,
        cancellation_requested_by=run.cancel_requested_by_label_snapshot,
        cancellation_reason=run.cancellation_reason,
        queued_at=run.queued_at,
        started_at=run.started_at,
        heartbeat_at=run.heartbeat_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _latest_current_previews(
    db: Session,
    *,
    policies: dict[str, LifecyclePolicy],
    requested_by_user_id: uuid.UUID | None,
    now: datetime,
) -> dict[str, LifecyclePreview]:
    previews: dict[str, LifecyclePreview] = {}
    for target_key, policy in policies.items():
        preview = db.scalar(
            select(LifecyclePreview)
            .where(
                LifecyclePreview.target_key == target_key,
                LifecyclePreview.requested_by_user_id == requested_by_user_id,
                LifecyclePreview.used_at.is_(None),
                LifecyclePreview.expires_at > now,
                LifecyclePreview.policy_revision == policy.revision,
                LifecyclePreview.request_fingerprint
                == _fingerprint(_policy_snapshot(policy)),
            )
            .order_by(LifecyclePreview.generated_at.desc(), LifecyclePreview.id.desc())
            .limit(1)
        )
        if preview is not None:
            previews[target_key] = preview
    return previews


def _set_policy_run_status(
    db: Session,
    run: LifecycleRun,
    *,
    current_time: datetime,
) -> None:
    policy = db.get(LifecyclePolicy, run.target_key)
    if policy is not None:
        policy.last_run_at = current_time
        policy.last_run_status = run.status
        db.add(policy)


def _user_labels(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all())


def _user_label(db: Session, user_id: uuid.UUID | None) -> str | None:
    return db.scalar(select(User.email).where(User.id == user_id)) if user_id else None


def _idempotency_hash(value: str) -> str:
    normalized = value.strip()
    if not (
        IDEMPOTENCY_KEY_MIN_LENGTH <= len(normalized) <= IDEMPOTENCY_KEY_MAX_LENGTH
    ):
        raise LifecycleValidationError(
            "Idempotency-Key must be between 8 and 255 characters."
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fingerprint(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


__all__ = [
    "LifecycleConflict",
    "LifecycleError",
    "LifecycleNotFound",
    "LifecyclePreviewConflict",
    "LifecycleRevisionConflict",
    "LifecycleValidationError",
    "cancel_lifecycle_run",
    "create_lifecycle_preview",
    "create_manual_lifecycle_run",
    "ensure_lifecycle_policies",
    "get_lifecycle_run",
    "lifecycle_policy_audit_snapshot",
    "lifecycle_policy_response_audit_snapshot",
    "lifecycle_preview_evidence",
    "lifecycle_overview",
    "list_lifecycle_runs",
    "update_lifecycle_policy",
]
