import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, local
from types import SimpleNamespace
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import reports as reports_routes
from app.models.audit_log import AuditLog
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_operation_receipt import ReportOperationReceipt
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.models.user import User
from app.schemas.reports import ReportTemplateCreate
from app.services.ai_context_budget import AIContextBudgetError
from app.services.export_query import ExportSnapshotChangedError


def _reporting_settings_stub():
    return SimpleNamespace(
        ai_enabled=True,
        ai_configured=True,
        reporting_enabled=True,
    )


def _report_payload(*, title: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "title": title,
        "period_start": (now - timedelta(days=7)).isoformat(),
        "period_end": now.isoformat(),
        "sections": [{"key": "executive_summary", "title": "Executive Summary"}],
    }


def _template_payload(*, name: str = "Private report template") -> dict:
    return {
        "name": name,
        "sections": [
            {
                "key": "executive_summary",
                "title": "Executive Summary",
                "enabled": True,
            }
        ],
    }


def _install_report_creation_stubs(monkeypatch):
    plan_calls = {"count": 0}
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )

    def _build_plan(*_args, **_kwargs):
        plan_calls["count"] += 1
        return object()

    def _create_report(db, *, user_id, payload, **kwargs):
        report = Report(
            owner_user_id=user_id,
            title=payload.title or "Generated report",
            report_type="custom",
            status="queued",
            trigger_source="manual",
            generation_stage="queued",
            request_idempotency_key=kwargs.get("request_idempotency_key"),
            request_idempotency_key_hash=kwargs.get(
                "request_idempotency_key_hash"
            ),
            request_fingerprint=kwargs.get("request_fingerprint"),
            period_start=payload.period_start,
            period_end=payload.period_end,
            filters_json=payload.filters.model_dump(mode="json"),
            prompt_config_json=payload.prompt.model_dump(mode="json"),
            sections_config_json=[
                section.model_dump(mode="json") for section in payload.sections
            ],
            metrics_json={},
            coverage_json={},
            source_count=1,
            included_source_count=1,
            estimated_input_tokens=100,
            generation_batches=1,
        )
        db.add(report)
        db.flush()
        return report

    monkeypatch.setattr(reports_routes, "build_report_source_plan", _build_plan)
    monkeypatch.setattr(reports_routes, "create_report_from_plan", _create_report)
    monkeypatch.setattr(
        reports_routes,
        "enqueue_report_task",
        lambda *, task_run_id, **_kwargs: f"report-{task_run_id}",
    )
    return plan_calls


def test_report_preview_returns_actionable_context_budget_error(
    client,
    auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )

    def _reject_plan(*_args, **_kwargs):
        raise AIContextBudgetError("The report objective leaves no room for evidence.")

    monkeypatch.setattr(reports_routes, "build_report_source_plan", _reject_plan)

    response = client.post(
        "/reports/preview",
        json={},
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "The report objective leaves no room for evidence."


def test_report_preview_returns_retryable_snapshot_conflict(
    client,
    auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )

    def _change_snapshot(*_args, **_kwargs):
        raise ExportSnapshotChangedError("source changed")

    monkeypatch.setattr(reports_routes, "build_report_source_plan", _change_snapshot)

    response = client.post(
        "/reports/preview",
        json={},
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Matching articles changed while report context was being prepared. "
        "Refresh the estimate and try again."
    )


def test_report_creation_returns_snapshot_conflict_without_persisting_report(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )

    def _change_snapshot(*_args, **_kwargs):
        raise ExportSnapshotChangedError("source changed")

    monkeypatch.setattr(reports_routes, "build_report_source_plan", _change_snapshot)
    now = datetime.now(timezone.utc)

    response = client.post(
        "/reports",
        json={
            "period_start": (now - timedelta(days=7)).isoformat(),
            "period_end": now.isoformat(),
            "sections": [
                {"key": "executive_summary", "title": "Executive Summary"}
            ],
        },
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Matching articles changed while the report was being prepared. "
        "Try generating it again."
    )
    assert db_session.query(Report).count() == 0


def test_report_creation_idempotency_replays_without_replanning(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    calls = _install_report_creation_stubs(monkeypatch)
    payload = _report_payload()
    raw_key = "report-create-test-key"
    headers = {**auth_headers["analyst"], "Idempotency-Key": raw_key}

    first = client.post("/reports", json=payload, headers=headers)
    second = client.post("/reports", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["report_id"] == first.json()["report_id"]
    assert second.json()["task_run_id"] == first.json()["task_run_id"]
    assert calls["count"] == 1
    assert db_session.query(Report).count() == 1
    report = db_session.get(Report, uuid.UUID(first.json()["report_id"]))
    assert report is not None
    assert report.request_idempotency_key == raw_key
    assert report.request_idempotency_key_hash != raw_key
    assert len(report.request_idempotency_key_hash or "") == 64
    assert len(report.request_fingerprint or "") == 64


def test_report_creation_idempotency_rejects_changed_payload(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    calls = _install_report_creation_stubs(monkeypatch)
    payload = _report_payload(title="First title")
    headers = {
        **auth_headers["analyst"],
        "Idempotency-Key": "report-create-conflict-key",
    }

    first = client.post("/reports", json=payload, headers=headers)
    changed = client.post(
        "/reports",
        json={**payload, "title": "Changed title"},
        headers=headers,
    )

    assert first.status_code == 202
    assert changed.status_code == 409
    assert "different report request" in changed.json()["detail"]
    assert calls["count"] == 1
    assert db_session.query(Report).count() == 1


def test_report_creation_rejects_blank_idempotency_key(
    client,
    db_session,
    auth_headers,
):
    response = client.post(
        "/reports",
        json=_report_payload(),
        headers={**auth_headers["analyst"], "Idempotency-Key": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Idempotency-Key must not be blank."
    assert db_session.query(Report).count() == 0


def test_report_creation_remains_accepted_when_broker_is_unavailable(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    _install_report_creation_stubs(monkeypatch)
    monkeypatch.setattr(
        reports_routes,
        "enqueue_report_task",
        lambda **_kwargs: None,
    )

    response = client.post(
        "/reports",
        json=_report_payload(),
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 202
    assert response.json()["celery_task_id"] is None
    report = db_session.get(Report, uuid.UUID(response.json()["report_id"]))
    run = db_session.get(AITaskRun, uuid.UUID(response.json()["task_run_id"]))
    assert report is not None and report.status == "queued"
    assert run is not None and run.status == "queued"


def test_template_creation_idempotency_replays_and_rejects_changed_payload(
    client,
    db_session,
    auth_headers,
):
    raw_key = "template-create-test-key"
    headers = {**auth_headers["analyst"], "Idempotency-Key": raw_key}
    payload = _template_payload()

    first = client.post("/reports/templates", json=payload, headers=headers)
    second = client.post("/reports/templates", json=payload, headers=headers)
    changed = client.post(
        "/reports/templates",
        json=_template_payload(name="Changed template"),
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert changed.status_code == 409
    assert "different data" in changed.json()["detail"]
    template_id = uuid.UUID(first.json()["id"])
    assert (
        db_session.query(ReportTemplate)
        .filter(ReportTemplate.id == template_id)
        .count()
        == 1
    )
    receipt = db_session.scalar(
        select(ReportOperationReceipt).where(
            ReportOperationReceipt.resource_id == template_id
        )
    )
    assert receipt is not None
    assert receipt.key_hash != raw_key
    assert len(receipt.key_hash) == 64
    assert len(receipt.fingerprint) == 64


def test_template_update_rejects_a_stale_resource_version(
    client,
    db_session,
    auth_headers,
):
    created = client.post(
        "/reports/templates",
        json=_template_payload(name="Versioned template"),
        headers=auth_headers["analyst"],
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    original_version = created.json()["updated_at"]
    versioned_headers = {
        **auth_headers["analyst"],
        "If-Match": f'"{original_version}"',
    }

    updated = client.put(
        f"/reports/templates/{template_id}",
        json=_template_payload(name="Current template"),
        headers=versioned_headers,
    )
    stale = client.put(
        f"/reports/templates/{template_id}",
        json=_template_payload(name="Stale overwrite"),
        headers=versioned_headers,
    )
    legacy_client_update = client.put(
        f"/reports/templates/{template_id}",
        json=_template_payload(name="Legacy client update"),
        headers=auth_headers["analyst"],
    )

    assert updated.status_code == 200
    assert updated.headers["etag"]
    assert stale.status_code == 412
    assert stale.json()["detail"] == (
        "The report template changed after you loaded it. Refresh the latest "
        "version, review the changes, and try again."
    )
    assert legacy_client_update.status_code == 200
    db_session.expire_all()
    assert db_session.get(ReportTemplate, uuid.UUID(template_id)).name == (
        "Legacy client update"
    )


def test_concurrent_template_creation_commits_one_resource_and_audit(
    database_engine,
    monkeypatch,
):
    run_id = uuid.uuid4().hex
    user_id = uuid.uuid4()
    session_factory = sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        class_=Session,
    )
    with session_factory.begin() as db:
        db.add(
            User(
                id=user_id,
                email=f"report-template-race-{run_id}@example.com",
                password_hash="not-a-login-secret",
                role="analyst",
                is_active=True,
                is_approved=True,
            )
        )

    actor = SimpleNamespace(id=user_id, role="analyst")
    payload = ReportTemplateCreate.model_validate(
        _template_payload(name=f"Concurrent template {run_id}")
    )
    idempotency_key = f"concurrent-template-{run_id}"
    first_lookup_barrier = Barrier(2)
    thread_state = local()
    find_operation_resource = reports_routes.find_operation_resource

    def _synchronized_find(*args, **kwargs):
        replay = find_operation_resource(*args, **kwargs)
        lookup_count = getattr(thread_state, "lookup_count", 0)
        thread_state.lookup_count = lookup_count + 1
        if lookup_count == 0:
            first_lookup_barrier.wait(timeout=5)
        return replay

    monkeypatch.setattr(
        reports_routes,
        "find_operation_resource",
        _synchronized_find,
    )

    def _create_template():
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            return reports_routes.create_template(
                payload,
                idempotency_key,
                db,
                actor,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_create_template) for _ in range(2)]
            responses = [future.result(timeout=15) for future in futures]

        assert responses[0].id == responses[1].id
        with session_factory() as db:
            assert db.scalar(
                select(func.count(ReportTemplate.id)).where(
                    ReportTemplate.owner_user_id == user_id
                )
            ) == 1
            assert db.scalar(
                select(func.count(ReportOperationReceipt.id)).where(
                    ReportOperationReceipt.actor_user_id == user_id
                )
            ) == 1
            assert db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.actor_user_id == user_id,
                    AuditLog.action == "reports.template.create",
                )
            ) == 1
    finally:
        with session_factory.begin() as db:
            db.execute(
                delete(ReportOperationReceipt).where(
                    ReportOperationReceipt.actor_user_id == user_id
                )
            )
            db.execute(
                delete(ReportTemplate).where(ReportTemplate.owner_user_id == user_id)
            )
            db.execute(delete(AuditLog).where(AuditLog.actor_user_id == user_id))
            db.execute(delete(User).where(User.id == user_id))


def test_template_clone_idempotency_survives_source_changes_and_reports_deletion(
    client,
    db_session,
    auth_headers,
):
    source_response = client.post(
        "/reports/templates",
        json=_template_payload(name="Clone source"),
        headers=auth_headers["analyst"],
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]
    headers = {
        **auth_headers["analyst"],
        "Idempotency-Key": "template-clone-test-key",
    }

    first = client.post(f"/reports/templates/{source_id}/clone", headers=headers)
    second = client.post(f"/reports/templates/{source_id}/clone", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert db_session.query(ReportTemplate).count() >= 2

    clone_id = first.json()["id"]
    deleted = client.delete(
        f"/reports/templates/{clone_id}", headers=auth_headers["analyst"]
    )
    replay_after_delete = client.post(
        f"/reports/templates/{source_id}/clone", headers=headers
    )

    assert deleted.status_code == 204
    assert replay_after_delete.status_code == 409
    assert "no longer exists" in replay_after_delete.json()["detail"]
    assert "new Idempotency-Key" in replay_after_delete.json()["detail"]


def test_report_retry_idempotency_replays_the_same_attempt(
    client,
    db_session,
    seed_users,
    auth_headers,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=seed_users["analyst"].id,
        title="Retry report",
        report_type="custom",
        status="error",
        trigger_source="manual",
        generation_stage="failed",
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    db_session.add(report)
    db_session.commit()
    monkeypatch.setattr(
        reports_routes,
        "enqueue_report_task",
        lambda *, task_run_id, **_kwargs: f"report-{task_run_id}",
    )
    headers = {
        **auth_headers["analyst"],
        "Idempotency-Key": "report-retry-test-key",
    }

    first = client.post(f"/reports/{report.id}/retry", headers=headers)
    second = client.post(f"/reports/{report.id}/retry", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["task_run_id"] == first.json()["task_run_id"]
    runs = db_session.query(AITaskRun).filter(AITaskRun.report_id == report.id).all()
    assert len(runs) == 1
    assert runs[0].request_idempotency_key_hash != "report-retry-test-key"


def test_analyst_cannot_retry_or_delete_another_users_report(
    client,
    db_session,
    seed_users,
    auth_headers,
):
    now = datetime.now(timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        owner_user_id=seed_users["admin"].id,
        title="Administrator report",
        report_type="custom",
        status="error",
        trigger_source="manual",
        generation_stage="failed",
        period_start=now - timedelta(days=7),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
    )
    db_session.add(report)
    db_session.commit()

    retry_response = client.post(
        f"/reports/{report.id}/retry",
        headers=auth_headers["analyst"],
    )
    delete_response = client.delete(
        f"/reports/{report.id}",
        headers=auth_headers["analyst"],
    )

    assert retry_response.status_code == 403
    assert delete_response.status_code == 403
    assert retry_response.json()["detail"] == "You can only retry or delete reports that you generated."
    assert db_session.get(Report, report.id) is not None


def test_admin_can_manage_report_schedule_with_default_filters(
    client,
    db_session,
    auth_headers,
):
    template = ReportTemplate(
        id=uuid.uuid4(),
        builtin_key="schedule-api-test",
        name="Schedule API template",
        description="Integration test template",
        report_type="weekly",
        visibility="shared",
        audience="security_team",
        objective="Summarize material security developments.",
        tone="analytical",
        detail_level="standard",
        use_company_context=True,
        focus_topics_json=[],
        excluded_topics_json=[],
        sections_json=[
            {"key": "executive_summary", "title": "Executive Summary", "enabled": True}
        ],
        default_filters_json={},
    )
    db_session.add(template)
    db_session.commit()
    payload = {
        "template_id": str(template.id),
        "name": "Weekly schedule API test",
        "enabled": False,
        "cadence": "weekly",
        "day_of_week": 1,
        "hour": 8,
        "minute": 15,
        "timezone": "Europe/Prague",
        "window_type": "previous_complete_week",
    }

    create_headers = {
        **auth_headers["admin"],
        "Idempotency-Key": "schedule-create-test-key",
    }
    create_response = client.post(
        "/reports/schedules", json=payload, headers=create_headers
    )
    replay_response = client.post(
        "/reports/schedules", json=payload, headers=create_headers
    )
    changed_response = client.post(
        "/reports/schedules",
        json={**payload, "name": "Changed schedule"},
        headers=create_headers,
    )

    assert create_response.status_code == 201
    schedule_id = create_response.json()["id"]
    assert replay_response.status_code == 201
    assert replay_response.json()["id"] == schedule_id
    assert changed_response.status_code == 409
    assert "different data" in changed_response.json()["detail"]
    assert db_session.query(ReportSchedule).count() == 1
    assert create_response.json()["filters"]["sort"] == "published_at_desc"
    assert create_response.json()["timezone"] == "Europe/Prague"

    payload["name"] = "Updated weekly schedule API test"
    update_headers = {
        **auth_headers["admin"],
        "If-Match": f'"{create_response.json()["updated_at"]}"',
    }
    update_response = client.put(
        f"/reports/schedules/{schedule_id}",
        json=payload,
        headers=update_headers,
    )
    stale_update_response = client.put(
        f"/reports/schedules/{schedule_id}",
        json={**payload, "name": "Stale schedule overwrite"},
        headers=update_headers,
    )
    legacy_update_response = client.put(
        f"/reports/schedules/{schedule_id}",
        json=payload,
        headers=auth_headers["admin"],
    )
    list_response = client.get(
        "/reports/schedules", headers=auth_headers["admin"]
    )

    assert update_response.status_code == 200
    assert update_response.headers["etag"]
    assert update_response.json()["name"] == payload["name"]
    assert stale_update_response.status_code == 412
    assert legacy_update_response.status_code == 200
    assert list_response.status_code == 200
    assert any(entry["id"] == schedule_id for entry in list_response.json())

    delete_response = client.delete(
        f"/reports/schedules/{schedule_id}", headers=auth_headers["admin"]
    )

    assert delete_response.status_code == 204
    assert db_session.get(ReportSchedule, uuid.UUID(schedule_id)) is None


def test_manual_schedule_run_maps_snapshot_race_to_conflict(
    client,
    auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )
    monkeypatch.setattr(
        reports_routes,
        "reserve_schedule_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExportSnapshotChangedError("snapshot changed")
        ),
    )

    response = client.post(
        f"/reports/schedules/{uuid.uuid4()}/run",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Matching articles changed while the scheduled report was being prepared. "
        "Try running the schedule again."
    )


def test_manual_schedule_run_idempotency_replays_existing_report(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    admin = db_session.scalar(select(User).where(User.role == "admin"))
    template = ReportTemplate(
        name=f"Manual run template {uuid.uuid4()}",
        description="",
        report_type="weekly",
        visibility="shared",
        audience="security_team",
        objective="Summarize material security developments.",
        tone="analytical",
        detail_level="standard",
        use_company_context=True,
        focus_topics_json=[],
        excluded_topics_json=[],
        sections_json=[
            {"key": "executive_summary", "title": "Executive Summary"}
        ],
        default_filters_json={},
    )
    db_session.add(template)
    db_session.flush()
    schedule = ReportSchedule(
        template_id=template.id,
        owner_user_id=admin.id,
        name=f"Manual run schedule {uuid.uuid4()}",
        enabled=True,
        cadence="weekly",
        day_of_week=0,
        day_of_month=1,
        hour=9,
        minute=0,
        timezone="UTC",
        window_type="previous_complete_week",
        rolling_days=7,
        filters_json={},
        delivery_enabled=False,
        delivery_mode="summary",
        skip_empty=True,
        missed_run_policy="latest",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(schedule)
    db_session.commit()
    calls = {"count": 0}

    def _reserve(db, **kwargs):
        calls["count"] += 1
        now = datetime.now(timezone.utc)
        report = Report(
            schedule_id=schedule.id,
            owner_user_id=admin.id,
            title="Manual schedule report",
            report_type="weekly",
            status="queued",
            trigger_source="scheduled",
            generation_stage="queued",
            generation_key=kwargs["generation_key_override"],
            request_idempotency_key_hash=kwargs["request_idempotency_key_hash"],
            request_fingerprint=kwargs["request_fingerprint"],
            period_start=now - timedelta(days=7),
            period_end=now,
            filters_json={},
            prompt_config_json={},
            sections_config_json=[],
            metrics_json={},
            coverage_json={},
        )
        db.add(report)
        db.flush()
        return [report]

    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )
    monkeypatch.setattr(reports_routes, "reserve_schedule_runs", _reserve)
    monkeypatch.setattr(
        reports_routes,
        "enqueue_report_task",
        lambda *, task_run_id, **_kwargs: f"report-{task_run_id}",
    )
    headers = {**auth_headers["admin"], "Idempotency-Key": "manual-week-32"}

    first = client.post(f"/reports/schedules/{schedule.id}/run", headers=headers)
    second = client.post(f"/reports/schedules/{schedule.id}/run", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()[0]["report_id"] == first.json()[0]["report_id"]
    assert second.json()[0]["task_run_id"] == first.json()[0]["task_run_id"]
    assert first.json()[0]["schedule_id"] == str(schedule.id)
    assert second.json()[0]["schedule_id"] == str(schedule.id)
    assert calls["count"] == 1


def test_manual_schedule_run_maps_invalid_legacy_configuration_to_422(
    client,
    auth_headers,
    monkeypatch,
):
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )
    monkeypatch.setattr(
        reports_routes,
        "reserve_schedule_runs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ZoneInfoNotFoundError("Missing/Legacy-Zone")
        ),
    )

    response = client.post(
        f"/reports/schedules/{uuid.uuid4()}/run",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 422
    assert "Missing/Legacy-Zone" in response.json()["detail"]


def test_manual_schedule_run_persists_missing_owner_quarantine(
    client,
    db_session,
    auth_headers,
    monkeypatch,
):
    template = ReportTemplate(
        name=f"Orphaned schedule template {uuid.uuid4()}",
        description="",
        report_type="weekly",
        visibility="shared",
        audience="security_team",
        objective="Summarize material security developments.",
        tone="analytical",
        detail_level="standard",
        use_company_context=True,
        focus_topics_json=[],
        excluded_topics_json=[],
        sections_json=[
            {"key": "executive_summary", "title": "Executive Summary"}
        ],
        default_filters_json={},
    )
    db_session.add(template)
    db_session.flush()
    schedule = ReportSchedule(
        template_id=template.id,
        owner_user_id=None,
        name=f"Orphaned schedule {uuid.uuid4()}",
        enabled=True,
        cadence="weekly",
        day_of_week=0,
        day_of_month=1,
        hour=9,
        minute=0,
        timezone="UTC",
        window_type="previous_complete_week",
        rolling_days=7,
        filters_json={},
        delivery_enabled=False,
        delivery_mode="summary",
        skip_empty=True,
        missed_run_policy="latest",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(schedule)
    db_session.commit()
    monkeypatch.setattr(
        reports_routes,
        "_active_reporting_settings",
        lambda _db: _reporting_settings_stub(),
    )

    response = client.post(
        f"/reports/schedules/{schedule.id}/run",
        headers=auth_headers["admin"],
    )

    assert response.status_code == 422
    assert "owner no longer exists" in response.json()["detail"]
    db_session.expire_all()
    stored = db_session.get(ReportSchedule, schedule.id)
    assert stored.failure_state == "quarantined"
    assert stored.last_error_code == "owner_missing"
    assert stored.enabled is False
    assert stored.next_run_at is None
