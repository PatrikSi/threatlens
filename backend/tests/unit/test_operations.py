from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.core.config import get_settings
from app.core.token_scopes import (
    ALLOWED_API_TOKEN_SCOPES,
    DEFAULT_API_TOKEN_SCOPES,
    SCOPE_READ_OPERATIONS,
    SCOPE_WRITE_OPERATIONS,
    missing_role_token_scopes,
)
from app.core.rbac import ROLE_ANALYST, ROLE_VIEWER
from app.models.integration import IntegrationDelivery, IntegrationInstance
from app.models.feed import Feed
from app.models.report import Report
from app.models.system_operation_run import SystemOperationRun
from app.schemas.health import (
    EncryptedDataInventoryCategory,
    EncryptedDataInventoryResponse,
    EncryptedDataInventorySummary,
    EncryptedDataStartupScan,
)
from app.services import (
    encrypted_data_inventory,
    operations,
    operations_probes,
    operations_projections,
)
from app.services.beat_heartbeat import BeatHealthSnapshot, BeatHeartbeatSnapshot


def _healthy_inventory(now: datetime) -> EncryptedDataInventoryResponse:
    empty = EncryptedDataInventoryCategory()
    return EncryptedDataInventoryResponse(
        ok=True,
        status="healthy",
        scanned_at=now,
        warnings=[],
        require_explicit_app_data_encryption_key=False,
        using_derived_app_data_encryption_key=False,
        startup_scan=EncryptedDataStartupScan(),
        feeds=empty,
        integration_secrets=empty,
        notification_webhooks=empty,
        notification_delivery_snapshots=empty,
        summary=EncryptedDataInventorySummary(),
    )


def _healthy_operations_inventory(
    now: datetime,
) -> encrypted_data_inventory.OperationsEncryptedDataInventory:
    return encrypted_data_inventory.OperationsEncryptedDataInventory(
        inventory=_healthy_inventory(now),
        row_limit_per_category=encrypted_data_inventory.OPERATIONS_INVENTORY_ROW_LIMIT,
        truncated_categories=(),
    )


def _install_healthy_probes(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(
        operations_probes.health_routes, "_database_health_ok", lambda _db: True
    )
    monkeypatch.setattr(
        operations_probes.health_routes, "_redis_health_ok", lambda _settings: True
    )

    def worker_snapshot(settings):
        required = operations_probes.health_routes._required_worker_queues(settings)
        return (
            True,
            {"worker@internal-host": "pong"},
            {
                "required": required,
                "covered": required,
                "missing": [],
                "by_worker": {"worker@internal-host": required},
            },
        )

    monkeypatch.setattr(
        operations_probes.health_routes, "_worker_health_snapshot", worker_snapshot
    )
    monkeypatch.setattr(
        operations_probes.health_routes,
        "_beat_health_snapshot",
        lambda _settings: BeatHealthSnapshot(
            scheduler=BeatHeartbeatSnapshot(True, now.isoformat(), 1, "healthy"),
            worker_round_trip=BeatHeartbeatSnapshot(
                True, now.isoformat(), 1, "healthy"
            ),
        ),
    )
    monkeypatch.setattr(
        operations_probes,
        "get_operations_encrypted_data_inventory",
        lambda _db, settings: _healthy_operations_inventory(now),
    )
    monkeypatch.setattr(
        operations_probes.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000_000, used=500_000, free=500_000),
    )


def test_operations_encrypted_inventory_is_bounded_and_cached(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        encrypted_data_inventory,
        "OPERATIONS_INVENTORY_ROW_LIMIT",
        2,
    )
    encrypted_data_inventory._clear_operations_encrypted_data_inventory_cache()
    for index in range(3):
        feed = Feed(name=f"Inventory feed {index}")
        feed.url = f"https://inventory-{index}.example.test/rss.xml"
        db_session.add(feed)
    db_session.commit()

    try:
        first = encrypted_data_inventory.get_operations_encrypted_data_inventory(
            db_session,
            settings=get_settings(),
        )
        second = encrypted_data_inventory.get_operations_encrypted_data_inventory(
            db_session,
            settings=get_settings(),
        )
    finally:
        encrypted_data_inventory._clear_operations_encrypted_data_inventory_cache()

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.row_limit_per_category == 2
    assert first.inventory.feeds.total_records == 2
    assert "feeds" in first.truncated_categories

    issues = []
    component = operations_probes._encrypted_data_component(
        db_session,
        get_settings(),
        datetime.now(timezone.utc),
        issues,
        database_ok=True,
    )
    assert component.status == "degraded"
    assert component.metrics["scan_complete"] is False
    assert "encrypted_data_inventory_truncated" in {entry.code for entry in issues}


def test_operations_scopes_are_explicit_and_not_delegated_to_non_admin_roles():
    assert SCOPE_READ_OPERATIONS in ALLOWED_API_TOKEN_SCOPES
    assert SCOPE_WRITE_OPERATIONS in ALLOWED_API_TOKEN_SCOPES
    assert SCOPE_READ_OPERATIONS not in DEFAULT_API_TOKEN_SCOPES
    assert SCOPE_WRITE_OPERATIONS not in DEFAULT_API_TOKEN_SCOPES
    assert missing_role_token_scopes(ROLE_ANALYST, [SCOPE_READ_OPERATIONS]) == [
        SCOPE_READ_OPERATIONS
    ]
    assert missing_role_token_scopes(ROLE_VIEWER, [SCOPE_WRITE_OPERATIONS]) == [
        SCOPE_WRITE_OPERATIONS
    ]


def test_operation_run_helpers_sanitize_and_finish_once(db_session):
    run = operations.create_system_operation_run(
        db_session,
        operation_type="backup",
        initiated_by="operator@example.com",
        source="offline_cli",
        metadata={
            "row_count": 42,
            "database_url": "postgresql://operator:password@db.internal/threatlens",
        },
    )
    db_session.commit()

    assert run.metadata_json == {"row_count": 42, "database_url": "[REDACTED]"}

    completed = operations.finish_system_operation_run(
        db_session,
        run_id=run.id,
        status="failed",
        error_code="archive_verify_failed",
        error_message="Could not read /srv/backups/private.dump with token=very-secret",
        metadata={"attempt": 1},
    )
    db_session.commit()

    assert completed.status == "failed"
    assert completed.finished_at is not None
    assert completed.metadata_json["attempt"] == 1
    assert "/srv/backups" not in (completed.error_message or "")
    assert "very-secret" not in (completed.error_message or "")

    repeated = operations.finish_system_operation_run(
        db_session, run_id=run.id, status="failed"
    )
    assert repeated.id == run.id
    with pytest.raises(ValueError, match="already complete"):
        operations.finish_system_operation_run(
            db_session, run_id=run.id, status="succeeded"
        )


def test_operation_run_helpers_reject_invalid_state(db_session):
    with pytest.raises(ValueError, match="Unsupported"):
        operations.create_system_operation_run(
            db_session,
            operation_type="shell",
            initiated_by="operator",
            source="cli",
        )
    with pytest.raises(ValueError, match="completion status"):
        operations.finish_system_operation_run(
            db_session,
            run_id=uuid.uuid4(),
            status="running",
        )

    run = operations.create_system_operation_run(
        db_session,
        operation_type="verify",
        initiated_by="operator",
        source="cli",
        started_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="cannot precede"):
        operations.finish_system_operation_run(
            db_session,
            run_id=run.id,
            status="failed",
            finished_at=run.started_at - timedelta(seconds=1),
        )


def test_operation_metadata_is_recursively_redacted_and_bounded():
    sanitized = operations.sanitize_operation_metadata(
        {
            "safe_count": 7,
            "database_url": "postgresql://admin:secret@db.internal/threatlens",
            "smtp_password": "mail-secret",
            "oidc_client_secret": "oidc-secret",
            "api_key": "api-key-secret",
            "private_key": "private-key-secret",
            "encryption_key": "encryption-key-secret",
            "credentials": {"safe-looking": "credential-secret"},
            "provider_credentials": "provider-credential-secret",
            "nested": {
                "path": "/var/lib/threatlens/private.dump",
                "message": (
                    "Bearer abc.def at https://auth.internal/application and "
                    "notify admin@example.com from db.internal:5432; "
                    "SMTP host=mail.internal and OIDC issuer=auth.internal; "
                    "read backups/private.dump from 192.0.2.10"
                ),
                "short_path": "/root",
            },
            "values": list(range(100)),
        }
    )

    rendered = str(sanitized)
    assert sanitized["safe_count"] == 7
    assert sanitized["database_url"] == "[REDACTED]"
    assert sanitized["smtp_password"] == "[REDACTED]"
    assert sanitized["oidc_client_secret"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["private_key"] == "[REDACTED]"
    assert sanitized["encryption_key"] == "[REDACTED]"
    assert sanitized["credentials"] == "[REDACTED]"
    assert sanitized["provider_credentials"] == "[REDACTED]"
    assert len(sanitized["values"]) == operations.MAX_METADATA_ENTRIES + 1
    for secret in (
        "admin:secret",
        "mail-secret",
        "oidc-secret",
        "api-key-secret",
        "private-key-secret",
        "encryption-key-secret",
        "credential-secret",
        "provider-credential-secret",
        "/var/lib/threatlens",
        "abc.def",
        "auth.internal",
        "admin@example.com",
        "db.internal:5432",
        "mail.internal",
        "backups/private.dump",
        "192.0.2.10",
        "/root",
    ):
        assert secret not in rendered


def test_overview_reports_delivery_and_report_backlogs(db_session, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _install_healthy_probes(monkeypatch, now)

    integration = IntegrationInstance(
        id=uuid.uuid4(),
        name="Operations test integration",
        integration_type="webhook",
        direction="outbound",
        enabled=True,
        config_json={},
    )
    db_session.add(integration)
    db_session.flush()
    db_session.add_all(
        [
            IntegrationDelivery(
                integration_id=integration.id,
                connector_type="webhook",
                event_type="item_processed",
                state="pending",
                idempotency_key=f"operations-pending-{uuid.uuid4()}",
                payload_json={},
                created_at=now - timedelta(minutes=15),
            ),
            IntegrationDelivery(
                integration_id=integration.id,
                connector_type="webhook",
                event_type="item_processed",
                state="sending",
                idempotency_key=f"operations-sending-{uuid.uuid4()}",
                payload_json={},
                claimed_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=10),
            ),
            IntegrationDelivery(
                integration_id=integration.id,
                connector_type="webhook",
                event_type="item_processed",
                state="dead_letter",
                idempotency_key=f"operations-failed-{uuid.uuid4()}",
                payload_json={},
                created_at=now - timedelta(minutes=5),
            ),
        ]
    )
    db_session.add_all(
        [
            _report(status="queued", now=now, queued_at=now - timedelta(hours=2)),
            _report(
                status="running",
                now=now,
                queued_at=now - timedelta(hours=1),
                started_at=now - timedelta(minutes=20),
                generation_lease_expires_at=now - timedelta(minutes=1),
            ),
            _report(status="error", now=now, queued_at=now - timedelta(hours=3)),
        ]
    )
    db_session.add_all(
        [
            _operation_run("backup", "succeeded", now - timedelta(hours=1)),
            _operation_run("restore_drill", "succeeded", now - timedelta(days=1)),
        ]
    )
    db_session.commit()

    overview = operations.collect_operations_overview(db_session, now=now)
    backlogs = {entry.key: entry for entry in overview.backlogs}

    assert overview.application.schema_current is True
    assert backlogs["integration_deliveries"].pending_count == 1
    assert backlogs["integration_deliveries"].active_count == 1
    assert backlogs["integration_deliveries"].stale_count == 1
    assert backlogs["integration_deliveries"].failed_count == 1
    assert backlogs["integration_deliveries"].oldest_pending_age_seconds == 900
    assert backlogs["reports"].pending_count == 1
    assert backlogs["reports"].active_count == 1
    assert backlogs["reports"].stale_count == 1
    assert backlogs["reports"].failed_count == 1
    assert backlogs["reports"].oldest_pending_age_seconds == 7200
    assert {issue.code for issue in overview.issues} >= {
        "integration_deliveries_stale",
        "reports_stale",
    }


def test_overview_degrades_without_leaking_probe_errors(db_session, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(
        operations_probes.health_routes, "_database_health_ok", lambda _db: True
    )
    monkeypatch.setattr(
        operations_probes.health_routes, "_redis_health_ok", lambda _settings: False
    )
    monkeypatch.setattr(
        operations_probes.health_routes,
        "_worker_health_snapshot",
        lambda settings: (
            False,
            {"worker@sensitive.internal": "pong"},
            {
                "required": operations_probes.health_routes._required_worker_queues(
                    settings
                ),
                "covered": [],
                "missing": operations_probes.health_routes._required_worker_queues(
                    settings
                ),
                "by_worker": {"worker@sensitive.internal": []},
            },
        ),
    )
    monkeypatch.setattr(
        operations_probes.health_routes,
        "_beat_health_snapshot",
        lambda _settings: (_ for _ in ()).throw(
            RuntimeError("redis://admin:secret@private.internal/0")
        ),
    )
    monkeypatch.setattr(
        operations_probes,
        "get_operations_encrypted_data_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("OIDC client_secret=do-not-leak")
        ),
    )
    monkeypatch.setattr(
        operations_probes.shutil,
        "disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("/private/storage/path")),
    )
    monkeypatch.setattr(
        operations_probes,
        "_load_database_size",
        lambda _db: (_ for _ in ()).throw(
            RuntimeError("postgresql://private.internal/db")
        ),
    )
    monkeypatch.setattr(
        operations_projections,
        "_load_delivery_backlog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("smtp_password=mail-secret")
        ),
    )
    monkeypatch.setattr(
        operations_projections,
        "_load_report_backlog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("/srv/reports/private")
        ),
    )
    monkeypatch.setattr(
        operations_projections,
        "_load_recovery_state",
        lambda _db: (_ for _ in ()).throw(RuntimeError("token=recovery-secret")),
    )

    overview = operations.collect_operations_overview(db_session, now=now)
    rendered = overview.model_dump_json()
    components = {entry.key: entry for entry in overview.components}

    assert overview.overall_status == "critical"
    assert components["redis"].status == "unavailable"
    assert components["workers"].status == "critical"
    assert components["workers"].metrics["worker_count"] == 1
    assert components["scheduler"].status == "unavailable"
    assert components["encrypted_data"].status == "unavailable"
    assert all(issue.effect and issue.recommended_action for issue in overview.issues)
    issue_codes = {issue.code for issue in overview.issues}
    assert "recovery_history_unavailable" in issue_codes
    assert "backup_not_recorded" not in issue_codes
    assert "restore_drill_not_recorded" not in issue_codes
    for secret in (
        "sensitive.internal",
        "admin:secret",
        "do-not-leak",
        "/private/storage",
        "private.internal",
        "mail-secret",
        "/srv/reports",
        "recovery-secret",
    ):
        assert secret not in rendered


def test_overview_skips_database_dependent_probes_when_database_is_unavailable(
    db_session,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _install_healthy_probes(monkeypatch, now)
    monkeypatch.setattr(
        operations_probes.health_routes, "_database_health_ok", lambda _db: False
    )
    encrypted_inventory_called = False

    def unexpected_inventory(*_args, **_kwargs):
        nonlocal encrypted_inventory_called
        encrypted_inventory_called = True
        raise AssertionError("database-dependent inventory should have been skipped")

    monkeypatch.setattr(
        operations_probes,
        "get_operations_encrypted_data_inventory",
        unexpected_inventory,
    )

    overview = operations.collect_operations_overview(db_session, now=now)
    components = {entry.key: entry for entry in overview.components}

    assert overview.overall_status == "critical"
    assert components["database"].status == "unavailable"
    assert components["encrypted_data"].status == "unknown"
    assert all(backlog.status == "unknown" for backlog in overview.backlogs)
    assert overview.application.schema_revision is None
    assert overview.application.schema_current is None
    assert encrypted_inventory_called is False
    issue_codes = {entry.code for entry in overview.issues}
    assert "database_unavailable" in issue_codes
    assert "backup_not_recorded" not in issue_codes


def _report(
    *,
    status: str,
    now: datetime,
    queued_at: datetime,
    started_at: datetime | None = None,
    generation_lease_expires_at: datetime | None = None,
) -> Report:
    return Report(
        id=uuid.uuid4(),
        title=f"Operations {status} report",
        status=status,
        generation_stage=status,
        period_start=now - timedelta(days=1),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        generation_context_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
        queued_at=queued_at,
        started_at=started_at,
        generation_lease_expires_at=generation_lease_expires_at,
    )


def _operation_run(
    operation_type: str,
    status: str,
    started_at: datetime,
    *,
    archive_sha256: str | None = None,
):
    return SystemOperationRun(
        id=uuid.uuid4(),
        operation_type=operation_type,
        status=status,
        initiated_by="pytest",
        source="test",
        metadata_json={"archive_sha256": archive_sha256} if archive_sha256 else {},
        started_at=started_at,
        finished_at=None if status == "running" else started_at + timedelta(minutes=1),
    )


def test_recovery_readiness_correlates_evidence_to_the_latest_archive(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    latest_checksum = "a" * 64
    older_checksum = "b" * 64
    db_session.add_all(
        [
            _operation_run(
                "backup",
                "succeeded",
                now - timedelta(hours=1),
                archive_sha256=latest_checksum,
            ),
            _operation_run(
                "verify",
                "succeeded",
                now - timedelta(minutes=30),
                archive_sha256=latest_checksum,
            ),
            _operation_run(
                "restore_drill",
                "succeeded",
                now - timedelta(days=2),
                archive_sha256=older_checksum,
            ),
        ]
    )
    db_session.commit()

    issues = []
    recovery = operations_projections.collect_recovery_snapshot(
        db_session,
        issues=issues,
        database_ok=True,
    )
    issue_codes = {entry.code for entry in issues}

    assert recovery.latest_backup is not None
    assert recovery.latest_backup.metadata["archive_sha256"] == latest_checksum
    assert "latest_backup_verify_mismatch" not in issue_codes
    assert "latest_backup_drill_mismatch" in issue_codes


def test_recovery_evidence_is_loaded_from_one_statement_snapshot(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    checksum = "8" * 64
    db_session.add_all(
        [
            _operation_run(
                "backup", "succeeded", now - timedelta(hours=2), archive_sha256=checksum
            ),
            _operation_run(
                "verify", "succeeded", now - timedelta(hours=1), archive_sha256=checksum
            ),
        ]
    )
    db_session.commit()
    statements: list[str] = []
    bind = db_session.get_bind()

    def capture_recovery_select(
        _conn, _cursor, statement, _parameters, _context, _many
    ):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "system_operation_runs" in statement
        ):
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", capture_recovery_select)
    try:
        recovery, correlation = operations_projections._load_recovery_state(db_session)
    finally:
        event.remove(bind, "before_cursor_execute", capture_recovery_select)

    assert len(statements) == 1
    assert recovery.latest_backup is not None
    assert correlation.verify is not None
    assert correlation.verify.metadata["archive_sha256"] == checksum


def test_recovery_readiness_reports_stale_success_and_incomplete_attempt(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db_session.add_all(
        [
            _operation_run(
                "backup",
                "succeeded",
                now - timedelta(days=3),
                archive_sha256="c" * 64,
            ),
            _operation_run(
                "restore_drill",
                "running",
                now - timedelta(hours=2),
                archive_sha256="c" * 64,
            ),
        ]
    )
    db_session.commit()

    issues = []
    operations_projections.collect_recovery_snapshot(
        db_session,
        issues=issues,
        database_ok=True,
    )
    issue_codes = {entry.code for entry in issues}

    assert "latest_backup_stale" in issue_codes
    assert "latest_restore_drill_incomplete" in issue_codes
    assert "latest_backup_not_verified" in issue_codes


def test_recovery_readiness_reports_stale_drill_for_latest_archive(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    checksum = "9" * 64
    db_session.add_all(
        [
            _operation_run(
                "backup",
                "succeeded",
                now - timedelta(hours=1),
                archive_sha256=checksum,
            ),
            _operation_run(
                "verify",
                "succeeded",
                now - timedelta(minutes=30),
                archive_sha256=checksum,
            ),
            _operation_run(
                "restore_drill",
                "succeeded",
                now - timedelta(days=32),
                archive_sha256=checksum,
            ),
        ]
    )
    db_session.commit()

    issues = []
    operations_projections.collect_recovery_snapshot(
        db_session,
        issues=issues,
        database_ok=True,
    )

    assert "latest_restore_drill_stale" in {entry.code for entry in issues}


def test_recovery_readiness_selects_matching_successful_evidence(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    backup_checksum = "d" * 64
    unrelated_checksum = "e" * 64
    db_session.add_all(
        [
            _operation_run(
                "backup",
                "succeeded",
                now - timedelta(hours=4),
                archive_sha256=backup_checksum,
            ),
            _operation_run(
                "backup",
                "failed",
                now - timedelta(minutes=30),
                archive_sha256=unrelated_checksum,
            ),
            _operation_run(
                "verify",
                "succeeded",
                now - timedelta(hours=3),
                archive_sha256=backup_checksum,
            ),
            _operation_run(
                "verify",
                "succeeded",
                now - timedelta(hours=1),
                archive_sha256=unrelated_checksum,
            ),
            _operation_run(
                "restore_drill",
                "succeeded",
                now - timedelta(hours=2),
                archive_sha256=backup_checksum,
            ),
        ]
    )
    db_session.commit()

    issues = []
    recovery = operations_projections.collect_recovery_snapshot(
        db_session,
        issues=issues,
        database_ok=True,
    )
    issue_codes = {entry.code for entry in issues}

    assert recovery.latest_backup is not None
    assert recovery.latest_backup.status == "failed"
    assert "latest_backup_failed" in issue_codes
    assert "latest_backup_not_verified" not in issue_codes
    assert "latest_backup_verify_mismatch" not in issue_codes
    assert "latest_backup_not_drilled" not in issue_codes
    assert "latest_backup_drill_mismatch" not in issue_codes


def test_recovery_readiness_ignores_failures_for_older_archives(db_session):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    latest_checksum = "f" * 64
    older_checksum = "0" * 64
    db_session.add_all(
        [
            _operation_run(
                "backup",
                "succeeded",
                now - timedelta(hours=4),
                archive_sha256=latest_checksum,
            ),
            _operation_run(
                "verify",
                "succeeded",
                now - timedelta(hours=3),
                archive_sha256=latest_checksum,
            ),
            _operation_run(
                "restore_drill",
                "succeeded",
                now - timedelta(hours=2),
                archive_sha256=latest_checksum,
            ),
            _operation_run(
                "verify",
                "failed",
                now - timedelta(hours=1),
                archive_sha256=older_checksum,
            ),
            _operation_run(
                "restore_drill",
                "failed",
                now - timedelta(minutes=30),
                archive_sha256=older_checksum,
            ),
        ]
    )
    db_session.commit()

    issues = []
    recovery = operations_projections.collect_recovery_snapshot(
        db_session,
        issues=issues,
        database_ok=True,
    )
    issue_codes = {entry.code for entry in issues}

    assert recovery.latest_verify is not None
    assert recovery.latest_verify.status == "succeeded"
    assert recovery.latest_restore_drill is not None
    assert recovery.latest_restore_drill.status == "succeeded"
    assert "latest_backup_verify_failed" not in issue_codes
    assert "latest_restore_drill_failed" not in issue_codes
