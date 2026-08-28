from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.security import generate_api_token
from app.core.token_scopes import SCOPE_READ_HEALTH, SCOPE_READ_OPERATIONS, SCOPE_WRITE_OPERATIONS
from app.models.api_token import ApiToken
from app.models.system_operation_run import SystemOperationRun
from app.main import app
from app.schemas.health import (
    EncryptedDataInventoryCategory,
    EncryptedDataInventoryResponse,
    EncryptedDataInventorySummary,
    EncryptedDataStartupScan,
)
from app.services import encrypted_data_inventory, operations, operations_probes
from app.services.beat_heartbeat import BeatHealthSnapshot, BeatHeartbeatSnapshot


OPERATIONS_PATHS = (
    "/operations/overview",
    "/operations/runs",
    "/operations/diagnostics",
)


@pytest.fixture()
def healthy_operations_probes(monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(operations_probes.health_routes, "_database_health_ok", lambda _db: True)
    monkeypatch.setattr(operations_probes.health_routes, "_redis_health_ok", lambda _settings: True)

    def worker_snapshot(settings):
        required = operations_probes.health_routes._required_worker_queues(settings)
        return (
            True,
            {"worker@test-host": "pong"},
            {
                "required": required,
                "covered": required,
                "missing": [],
                "by_worker": {"worker@test-host": required},
            },
        )

    monkeypatch.setattr(operations_probes.health_routes, "_worker_health_snapshot", worker_snapshot)
    monkeypatch.setattr(
        operations_probes.health_routes,
        "_beat_health_snapshot",
        lambda _settings: BeatHealthSnapshot(
            scheduler=BeatHeartbeatSnapshot(True, now.isoformat(), 1, "healthy"),
            worker_round_trip=BeatHeartbeatSnapshot(True, now.isoformat(), 1, "healthy"),
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
    return now


def test_operations_endpoints_require_authentication_and_admin_role(
    client,
    auth_headers,
    healthy_operations_probes,
):
    _ = healthy_operations_probes
    for path in OPERATIONS_PATHS:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth_headers["viewer"]).status_code == 403
        assert client.get(path, headers=auth_headers["analyst"]).status_code == 403
        response = client.get(path, headers=auth_headers["admin"])
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_operations_endpoints_require_explicit_token_scope(
    client,
    db_session,
    seed_users,
    healthy_operations_probes,
):
    _ = healthy_operations_probes
    admin = seed_users["admin"]
    read_headers = _token_headers(db_session, admin.id, [SCOPE_READ_OPERATIONS])
    health_headers = _token_headers(db_session, admin.id, [SCOPE_READ_HEALTH])
    write_headers = _token_headers(db_session, admin.id, [SCOPE_WRITE_OPERATIONS])
    analyst_headers = _token_headers(db_session, seed_users["analyst"].id, [SCOPE_READ_OPERATIONS])

    for path in OPERATIONS_PATHS:
        assert client.get(path, headers=read_headers).status_code == 200
        assert client.get(path, headers=health_headers).status_code == 403
    assert client.get("/operations/runs", headers=write_headers).status_code == 200
    assert client.get("/operations/runs", headers=analyst_headers).status_code == 403


def test_admin_cookie_session_can_read_operations(
    client,
    seed_users,
    healthy_operations_probes,
):
    _ = seed_users, healthy_operations_probes
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )
    assert login.status_code == 200

    response = client.get("/operations/overview")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_operation_run_history_is_paginated_filtered_and_redacted(
    client,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = []
    for index in range(5):
        row = SystemOperationRun(
            id=uuid.uuid4(),
            operation_type="backup" if index % 2 == 0 else "verify",
            status="failed" if index == 4 else "succeeded",
            initiated_by="operator@example.com",
            source="offline_cli",
            metadata_json={"sequence": index},
            error_code="backup_failed" if index == 4 else None,
            error_message=(
                "Failed at /srv/backups/private.dump using "
                "postgresql://admin:password@db.internal/threatlens token=secret-value"
                if index == 4
                else None
            ),
            started_at=now - timedelta(minutes=index),
            finished_at=now - timedelta(minutes=index) + timedelta(seconds=30),
        )
        rows.append(row)
        db_session.add(row)
    rows[4].metadata_json = {
        "database_url": "postgresql://admin:password@db.internal/threatlens",
        "smtp_password": "mail-secret",
        "safe_count": 9,
        "values": list(range(100)),
    }
    db_session.commit()

    first = client.get("/operations/runs?page=1&page_size=2", headers=auth_headers["admin"])
    second = client.get("/operations/runs?page=2&page_size=2", headers=auth_headers["admin"])
    failed = client.get("/operations/runs?status=failed", headers=auth_headers["admin"])
    backups = client.get("/operations/runs?operation_type=backup", headers=auth_headers["admin"])

    assert first.status_code == 200
    assert first.json()["total"] == 5
    assert [entry["id"] for entry in first.json()["runs"]] == [str(rows[0].id), str(rows[1].id)]
    assert [entry["id"] for entry in second.json()["runs"]] == [str(rows[2].id), str(rows[3].id)]
    assert failed.json()["total"] == 1
    assert backups.json()["total"] == 3
    failed_run = failed.json()["runs"][0]
    assert failed_run["metadata"]["safe_count"] == 9
    assert failed_run["metadata"]["database_url"] == "[REDACTED]"
    assert failed_run["metadata"]["smtp_password"] == "[REDACTED]"
    assert len(failed_run["metadata"]["values"]) == operations.MAX_METADATA_ENTRIES + 1
    rendered = failed.text
    for secret in (
        "/srv/backups",
        "admin:password",
        "db.internal",
        "secret-value",
        "mail-secret",
    ):
        assert secret not in rendered
    assert client.get("/operations/runs?page=0", headers=auth_headers["admin"]).status_code == 422
    assert (
        client.get("/operations/runs?page=1000001", headers=auth_headers["admin"]).status_code
        == 422
    )
    assert client.get("/operations/runs?page_size=101", headers=auth_headers["admin"]).status_code == 422


def test_diagnostics_snapshot_is_stable_bounded_and_redacted(
    client,
    auth_headers,
    db_session,
    healthy_operations_probes,
):
    now = healthy_operations_probes
    for index in range(30):
        db_session.add(
            SystemOperationRun(
                id=uuid.uuid4(),
                operation_type="diagnostics",
                status="succeeded",
                initiated_by="pytest",
                source="test",
                metadata_json={
                    "sequence": index,
                    "oidc_client_secret": "never-return-this",
                    "values": list(range(100)),
                },
                started_at=now - timedelta(seconds=index),
                finished_at=now - timedelta(seconds=index) + timedelta(milliseconds=10),
            )
        )
    db_session.commit()

    response = client.get("/operations/diagnostics", headers=auth_headers["admin"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["generated_at"] == payload["overview"]["generated_at"]
    assert len(payload["recent_runs"]) == operations.DIAGNOSTIC_RUN_LIMIT
    assert payload["recent_runs_truncated"] is True
    assert len(response.content) < 1_000_000
    assert "never-return-this" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_operations_overview_returns_redacted_degraded_200_when_advisory_probes_fail(
    client,
    auth_headers,
    monkeypatch,
    healthy_operations_probes,
):
    _ = healthy_operations_probes
    monkeypatch.setattr(
        operations_probes,
        "get_operations_encrypted_data_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("client_secret=private-value")),
    )
    monkeypatch.setattr(
        operations_probes.shutil,
        "disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("/private/filesystem/path")),
    )
    monkeypatch.setattr(
        operations_probes,
        "_load_database_size",
        lambda _db: (_ for _ in ()).throw(RuntimeError("postgresql://admin:secret@private.internal/db")),
    )

    response = client.get("/operations/overview", headers=auth_headers["admin"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "degraded"
    components = {entry["key"]: entry for entry in payload["components"]}
    storage = {entry["key"]: entry for entry in payload["storage"]}
    assert components["database"]["status"] == "healthy"
    assert components["redis"]["status"] == "healthy"
    assert components["workers"]["status"] == "healthy"
    assert components["scheduler"]["status"] == "healthy"
    assert components["encrypted_data"]["status"] == "unavailable"
    assert storage["database"]["status"] == "unknown"
    assert storage["application_filesystem"]["status"] == "unknown"
    assert all(
        issue["effect"] and issue["recommended_action"]
        for issue in payload["issues"]
    )
    for secret in ("admin:secret", "private.internal", "private-value", "/private/filesystem"):
        assert secret not in response.text


def test_existing_health_payloads_remain_compatible(
    client,
    auth_headers,
    healthy_operations_probes,
):
    _ = healthy_operations_probes
    public = client.get("/health")
    detailed = client.get("/health", headers=auth_headers["admin"])
    worker = client.get("/health/worker", headers=auth_headers["admin"])
    beat = client.get("/health/beat", headers=auth_headers["admin"])

    assert public.status_code == 200
    assert public.json() == {"ok": True}
    assert detailed.status_code == 200
    assert detailed.json() == {
        "ok": True,
        "db": True,
        "redis": True,
        "worker": True,
        "beat": True,
    }
    assert worker.status_code == 200
    assert worker.json()["ok"] is True
    assert worker.json()["workers"] == {"worker@test-host": "pong"}
    assert worker.json()["queues"]["missing"] == []
    assert beat.status_code == 200
    assert beat.json()["ok"] is True
    assert beat.json()["reason"] == "healthy"
    assert "heartbeat_key" in beat.json()
    assert "scheduler_heartbeat_key" in beat.json()


def test_operations_openapi_is_read_only_and_declares_required_scope():
    schema = app.openapi()
    for path in (
        "/v1/operations/overview",
        "/v1/operations/runs",
        "/v1/operations/diagnostics",
    ):
        assert set(schema["paths"][path]) == {"get"}
        assert schema["paths"][path]["get"]["x-threatlens-required-token-scopes"] == [
            SCOPE_READ_OPERATIONS
        ]


def _token_headers(db_session, user_id: uuid.UUID, scopes: list[str]) -> dict[str, str]:
    token_value, token_prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            id=uuid.uuid4(),
            user_id=user_id,
            name=f"operations-{uuid.uuid4()}",
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.flush()
    return {"Authorization": f"Bearer {token_value}"}


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
