from __future__ import annotations

import shutil
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import health as health_routes
from app.core.config import Settings
from app.schemas.operations import (
    OperationsApplicationInfo,
    OperationsComponentCheck,
    OperationsIssue,
    OperationsStorageIndicator,
)
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.operations_common import issue, safe_db_probe, safe_probe
from app.services.operations_redaction import safe_reason, safe_revision, safe_string_list
from app.version import get_app_version


_BACKEND_DIR = Path(__file__).resolve().parents[2]


def collect_component_checks(
    db: Session,
    *,
    settings: Settings,
    checked_at: datetime,
    issues: list[OperationsIssue],
) -> tuple[bool, list[OperationsComponentCheck]]:
    database_ok = health_routes._database_health_ok(db)
    components = [_database_component(database_ok, checked_at)]
    if not database_ok:
        issues.append(
            issue(
                "database_unavailable",
                "critical",
                "database",
                "The database health check failed.",
                "Authenticated requests and durable background work may fail.",
                "Restore PostgreSQL connectivity, then run the readiness and operations checks again.",
            )
        )
    components.extend(
        [
            _redis_component(settings, checked_at, issues),
            _worker_component(settings, checked_at, issues),
            _beat_component(settings, checked_at, issues),
            _encrypted_data_component(
                db,
                settings,
                checked_at,
                issues,
                database_ok=database_ok,
            ),
        ]
    )
    return database_ok, components


def collect_application_info(
    db: Session,
    *,
    database_ok: bool,
    issues: list[OperationsIssue],
) -> OperationsApplicationInfo:
    schema_revision = None
    if database_ok:
        schema_revision = safe_db_probe(
            db,
            "schema_revision",
            lambda: _load_schema_revision(db),
            None,
        )
    expected_revision = safe_probe(
        "packaged_schema_revision",
        _load_packaged_schema_revision,
        None,
    )
    schema_current = (
        schema_revision == expected_revision
        if schema_revision is not None and expected_revision is not None
        else None
    )
    if expected_revision is None:
        issues.append(
            issue(
                "packaged_schema_revision_unavailable",
                "warning",
                "application",
                "The packaged migration head could not be determined.",
                "Application and database schema compatibility cannot be compared.",
                "Verify the image contains one complete Alembic migration chain.",
            )
        )
    if database_ok and schema_revision is None:
        issues.append(
            issue(
                "schema_revision_unavailable",
                "warning",
                "database",
                "The applied database revision could not be determined.",
                "Deployment and schema compatibility cannot be confirmed from this snapshot.",
                "Run the Alembic current and check commands from the application container.",
            )
        )
    elif schema_current is False:
        issues.append(
            issue(
                "schema_revision_mismatch",
                "critical",
                "database",
                "The application and database schema revisions do not match.",
                "Requests may encounter missing or incompatible database objects.",
                "Apply the bundled migrations before serving application traffic.",
            )
        )
    return OperationsApplicationInfo(
        version=get_app_version(),
        schema_revision=schema_revision,
        expected_schema_revision=expected_revision or "unknown",
        schema_current=schema_current,
    )


def collect_storage_indicators(
    db: Session,
    *,
    issues: list[OperationsIssue],
    database_ok: bool,
) -> list[OperationsStorageIndicator]:
    database_size = None
    if database_ok:
        database_size = safe_db_probe(
            db,
            "database_storage",
            lambda: _load_database_size(db),
            None,
        )
    database_indicator = OperationsStorageIndicator(
        key="database",
        label="Database storage",
        status="healthy" if database_size is not None else "unknown",
        used_bytes=database_size,
    )
    if database_ok and database_size is None:
        issues.append(
            issue(
                "database_storage_unavailable",
                "warning",
                "storage",
                "Database size could not be measured.",
                "Storage growth cannot be assessed from this snapshot.",
                "Check database monitoring permissions and external capacity metrics.",
            )
        )

    usage = safe_probe("filesystem_storage", _load_filesystem_usage, None)
    if usage is None:
        filesystem_indicator = OperationsStorageIndicator(
            key="application_filesystem",
            label="Application filesystem",
            status="unknown",
        )
        issues.append(
            issue(
                "filesystem_storage_unavailable",
                "warning",
                "storage",
                "Application filesystem capacity could not be measured.",
                "Local temporary-file and artifact capacity cannot be assessed.",
                "Check host or container storage monitoring.",
            )
        )
    else:
        total_bytes, used_bytes, available_bytes = usage
        percent_used = round((used_bytes / total_bytes) * 100, 1)
        status = "critical" if percent_used >= 95 else "degraded" if percent_used >= 85 else "healthy"
        filesystem_indicator = OperationsStorageIndicator(
            key="application_filesystem",
            label="Application filesystem",
            status=status,
            used_bytes=used_bytes,
            total_bytes=total_bytes,
            available_bytes=available_bytes,
            percent_used=percent_used,
        )
        if status != "healthy":
            issues.append(
                issue(
                    "filesystem_capacity_low",
                    "critical" if status == "critical" else "warning",
                    "storage",
                    "Application filesystem capacity is low.",
                    "Exports, temporary files, or container writes may fail.",
                    "Free storage and verify database and artifact retention policies.",
                )
            )
    return [database_indicator, filesystem_indicator]


def _database_component(database_ok: bool, checked_at: datetime) -> OperationsComponentCheck:
    return OperationsComponentCheck(
        key="database",
        label="Database",
        status="healthy" if database_ok else "unavailable",
        summary="PostgreSQL accepted a query." if database_ok else "PostgreSQL did not accept the health query.",
        checked_at=checked_at,
    )


def _redis_component(
    settings: Settings,
    checked_at: datetime,
    issues: list[OperationsIssue],
) -> OperationsComponentCheck:
    redis_ok = safe_probe("redis", lambda: health_routes._redis_health_ok(settings), False)
    if not redis_ok:
        issues.append(
            issue(
                "redis_unavailable",
                "critical",
                "redis",
                "Redis did not answer the health probe.",
                "Task dispatch, rate limits, and ephemeral coordination may be unavailable.",
                "Restore Redis connectivity; Redis data itself should be treated as reconstructible.",
            )
        )
    return OperationsComponentCheck(
        key="redis",
        label="Redis",
        status="healthy" if redis_ok else "unavailable",
        summary="Redis answered the health probe." if redis_ok else "Redis did not answer the health probe.",
        checked_at=checked_at,
    )


def _worker_component(
    settings: Settings,
    checked_at: datetime,
    issues: list[OperationsIssue],
) -> OperationsComponentCheck:
    required = health_routes._required_worker_queues(settings)
    fallback = (False, {}, {"required": required, "covered": [], "missing": required})
    worker_ok, worker_count, queues = safe_probe(
        "workers",
        lambda: _normalize_worker_snapshot(
            health_routes._worker_health_snapshot(settings)
        ),
        (False, 0, fallback[2]),
    )
    required_queues = safe_string_list(queues.get("required"), fallback=required)
    covered_queues = safe_string_list(queues.get("covered"), fallback=[])
    missing_queues = safe_string_list(queues.get("missing"), fallback=required)
    if not worker_ok:
        issues.append(
            issue(
                "required_workers_unavailable",
                "critical",
                "workers",
                "One or more required task queues have no healthy worker.",
                "Ingestion, processing, notifications, maintenance, or AI work may remain queued.",
                "Restore workers for every reported missing queue and rerun the worker health check.",
            )
        )
    return OperationsComponentCheck(
        key="workers",
        label="Workers",
        status="healthy" if worker_ok else "critical",
        summary="All required queues are covered." if worker_ok else "Required queue coverage is incomplete.",
        checked_at=checked_at,
        metrics={
            "worker_count": worker_count,
            "required_queues": required_queues,
            "covered_queues": covered_queues,
            "missing_queues": missing_queues,
        },
    )


def _normalize_worker_snapshot(raw: object) -> tuple[bool, int, dict]:
    if not isinstance(raw, tuple) or len(raw) != 3:
        raise ValueError("Invalid worker health snapshot")
    worker_ok, workers, queues = raw
    if not isinstance(worker_ok, bool) or not isinstance(workers, dict) or not isinstance(queues, dict):
        raise ValueError("Invalid worker health snapshot")
    return worker_ok, len(workers), queues


def _beat_component(
    settings: Settings,
    checked_at: datetime,
    issues: list[OperationsIssue],
) -> OperationsComponentCheck:
    snapshot = safe_probe(
        "beat",
        lambda: _validate_beat_snapshot(health_routes._beat_health_snapshot(settings)),
        None,
    )
    if snapshot is None:
        issues.append(
            issue(
                "scheduler_probe_unavailable",
                "critical",
                "scheduler",
                "The scheduler heartbeat probe could not be completed.",
                "Periodic dispatch and recovery work cannot be confirmed.",
                "Check Redis and the beat and maintenance worker processes.",
            )
        )
        return OperationsComponentCheck(
            key="scheduler",
            label="Scheduler",
            status="unavailable",
            summary="Scheduler heartbeat information is unavailable.",
            checked_at=checked_at,
        )

    scheduler, round_trip = snapshot
    if not round_trip.ok:
        status = "critical"
        summary = "The scheduler-to-worker heartbeat is not healthy."
        issues.append(
            issue(
                "scheduler_round_trip_unhealthy",
                "critical",
                "scheduler",
                summary,
                "Periodic tasks may not be reaching a maintenance worker.",
                "Check beat, Redis, and the maintenance worker before relying on scheduled work.",
            )
        )
    elif not scheduler.ok:
        status = "degraded"
        summary = "Worker round-trip is healthy, but the scheduler heartbeat is not."
        issues.append(
            issue(
                "scheduler_heartbeat_unhealthy",
                "warning",
                "scheduler",
                "The scheduler process heartbeat is not healthy.",
                "Periodic work may stop once already-published tasks are exhausted.",
                "Check the beat process and confirm its heartbeat advances.",
            )
        )
    else:
        status = "healthy"
        summary = "Scheduler and worker round-trip heartbeats are healthy."
    return OperationsComponentCheck(
        key="scheduler",
        label="Scheduler",
        status=status,
        summary=summary,
        checked_at=checked_at,
        metrics={
            "worker_round_trip_age_seconds": round_trip.age_seconds,
            "worker_round_trip_reason": safe_reason(round_trip.reason),
            "scheduler_age_seconds": scheduler.age_seconds,
            "scheduler_reason": safe_reason(scheduler.reason),
            "stale_after_seconds": int(settings.beat_heartbeat_stale_after_seconds),
        },
    )


def _validate_beat_snapshot(snapshot):
    scheduler = snapshot.scheduler
    round_trip = snapshot.worker_round_trip
    for heartbeat in (scheduler, round_trip):
        if not isinstance(heartbeat.ok, bool):
            raise ValueError("Invalid beat health snapshot")
        if heartbeat.age_seconds is not None and not isinstance(heartbeat.age_seconds, int):
            raise ValueError("Invalid beat health snapshot")
    return scheduler, round_trip


def _encrypted_data_component(
    db: Session,
    settings: Settings,
    checked_at: datetime,
    issues: list[OperationsIssue],
    *,
    database_ok: bool,
) -> OperationsComponentCheck:
    if not database_ok:
        return OperationsComponentCheck(
            key="encrypted_data",
            label="Encrypted data",
            status="unknown",
            summary="Encrypted-data inventory was not checked because the database is unavailable.",
            checked_at=checked_at,
        )
    inventory = safe_db_probe(
        db,
        "encrypted_data",
        lambda: scan_encrypted_data_inventory(db, settings=settings),
        None,
    )
    if inventory is None:
        issues.append(
            issue(
                "encrypted_data_probe_unavailable",
                "warning",
                "encrypted_data",
                "The encrypted-data inventory could not be completed.",
                "Unreadable encrypted configuration cannot be ruled out.",
                "Run the dedicated encrypted-data health check and inspect server logs by request ID.",
            )
        )
        return OperationsComponentCheck(
            key="encrypted_data",
            label="Encrypted data",
            status="unavailable",
            summary="Encrypted-data inventory is unavailable.",
            checked_at=checked_at,
        )

    status = {"healthy": "healthy", "warning": "degraded", "critical": "critical"}.get(
        inventory.status,
        "unknown",
    )
    if inventory.summary.unreadable_fields:
        issues.append(
            issue(
                "encrypted_data_unreadable",
                "critical",
                "encrypted_data",
                "One or more encrypted fields cannot be decrypted.",
                "Feeds or integrations that depend on those values may fail.",
                "Restore the matching application data-encryption key before modifying affected records.",
            )
        )
    if inventory.using_derived_app_data_encryption_key:
        issues.append(
            issue(
                "derived_encryption_key",
                "warning",
                "encrypted_data",
                "A derived development encryption key is in use.",
                "Encrypted data may become unreadable after configuration changes.",
                "Configure an explicit persistent application data-encryption key.",
            )
        )
    return OperationsComponentCheck(
        key="encrypted_data",
        label="Encrypted data",
        status=status,
        summary=(
            "All inventoried encrypted fields are readable."
            if inventory.summary.unreadable_fields == 0
            else "Some inventoried encrypted fields are unreadable."
        ),
        checked_at=checked_at,
        metrics={
            "total_records": inventory.summary.total_records,
            "encrypted_records": inventory.summary.encrypted_records,
            "unreadable_records": inventory.summary.unreadable_records,
            "unreadable_fields": inventory.summary.unreadable_fields,
            "using_derived_key": inventory.using_derived_app_data_encryption_key,
        },
    )


def _load_schema_revision(db: Session) -> str | None:
    return safe_revision(db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1")))


@lru_cache(maxsize=1)
def _load_packaged_schema_revision() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    return safe_revision(heads[0]) if len(heads) == 1 else None


def _load_database_size(db: Session) -> int | None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return None
    value = db.scalar(text("SELECT pg_database_size(current_database())"))
    return max(0, int(value)) if value is not None else None


def _load_filesystem_usage() -> tuple[int, int, int]:
    usage = shutil.disk_usage("/")
    total_bytes = int(usage.total)
    if total_bytes <= 0:
        raise ValueError("Filesystem capacity is not positive")
    used_bytes = min(total_bytes, max(0, int(usage.used)))
    available_bytes = min(total_bytes, max(0, int(usage.free)))
    return total_bytes, used_bytes, available_bytes
