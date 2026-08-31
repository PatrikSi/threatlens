from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.audit_log import AuditLog
from app.models.data_policy import (
    DataAccessEnvelope,
    DataPolicyState,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.iam import IAMPolicyState
from app.models.report import Report
from app.models.user import User
from app.services import data_access_policy
from app.services.ai_egress_data_policy import (
    AIEgressPolicyError,
    enforce_ai_egress_data_policy,
)
from app.services.ai_provider_attempts import (
    AIProviderAttemptStateError,
    void_ai_provider_attempt_reservation,
)
from app.services.ai_provider_client import AICompletionResult
from app.services.ai_request_runtime import (
    AIProviderAttemptReplayBlockedError,
    run_ai_json_request,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessSourceInput,
    merge_data_access_envelope_sources,
)


def test_concurrent_ai_egress_denials_create_one_audit_decision(
    database_engine,
    monkeypatch,
    request,
):
    owner_id = uuid.uuid4()
    report_id = uuid.uuid4()
    application_prefix = f"threatlens-ai-egress-{uuid.uuid4()}"
    first_decision_ready = threading.Event()
    release_first = threading.Event()
    start = threading.Barrier(2)
    completed = threading.Event()
    errors: list[BaseException] = []
    decision_order = 0
    decision_order_lock = threading.Lock()

    with Session(database_engine) as setup_db:
        state = setup_db.scalar(
            select(DataPolicyState).where(DataPolicyState.id == 1).with_for_update()
        )
        assert state is not None
        original_state = (
            state.mode,
            state.revision,
            state.coverage_version,
            state.enforced_at,
            state.enforced_by_user_id,
            state.updated_by_user_id,
        )
        owner = User(
            id=owner_id,
            email=f"ai-egress-{owner_id}@example.com",
            password_hash="not-used",
            role="viewer",
            is_active=False,
            is_approved=True,
        )
        now = datetime.now(timezone.utc)
        report = Report(
            id=report_id,
            owner_user_id=owner_id,
            title="Concurrent AI egress audit",
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(report)
        state.mode = "audit"
        state.coverage_version = 1
        state.revision += 1
        state.enforced_at = None
        state.enforced_by_user_id = None
        setup_db.flush()
        merge_data_access_envelope_sources(
            setup_db,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
            sources=(
                DataAccessSourceInput(
                    source_type="test_fixture",
                    source_id=str(uuid.uuid4()),
                    source_version="1",
                    handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                    captured_policy_revision=state.revision,
                ),
            ),
        )
        setup_db.commit()

    def cleanup() -> None:
        release_first.set()
        for worker in workers:
            worker.join(timeout=6)
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AuditLog).where(
                    AuditLog.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    AuditLog.resource_id == str(report_id),
                )
            )
            cleanup_db.execute(
                delete(DataAccessEnvelope).where(
                    DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    DataAccessEnvelope.resource_id == report_id,
                )
            )
            cleanup_db.execute(delete(Report).where(Report.id == report_id))
            cleanup_db.execute(delete(User).where(User.id == owner_id))
            state = cleanup_db.scalar(
                select(DataPolicyState).where(DataPolicyState.id == 1).with_for_update()
            )
            assert state is not None
            (
                state.mode,
                state.revision,
                state.coverage_version,
                state.enforced_at,
                state.enforced_by_user_id,
                state.updated_by_user_id,
            ) = original_state
            cleanup_db.commit()

    workers: list[threading.Thread] = []
    request.addfinalizer(cleanup)

    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )

    def authorize(worker_number: int) -> None:
        nonlocal decision_order
        try:
            with Session(database_engine) as worker_db:
                worker_db.execute(
                    select(text("set_config('application_name', :name, false)")),
                    {"name": f"{application_prefix}-{worker_number}"},
                )
                start.wait(timeout=3)
                try:
                    enforce_ai_egress_data_policy(
                        worker_db,
                        feature_type="report",
                        item_id=None,
                        daily_brief_id=None,
                        report_id=report_id,
                    )
                except AIEgressPolicyError as exc:
                    assert exc.retryable is False
                else:  # pragma: no cover - defensive assertion in worker
                    raise AssertionError("inactive report owner must be denied")
                with decision_order_lock:
                    decision_order += 1
                    position = decision_order
                if position == 1:
                    first_decision_ready.set()
                    assert release_first.wait(timeout=5)
                worker_db.commit()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            if worker_number == 2:
                completed.set()

    workers.extend(
        threading.Thread(target=authorize, args=(number,), daemon=True)
        for number in (1, 2)
    )
    try:
        for worker in workers:
            worker.start()
        assert first_decision_ready.wait(timeout=5)
        deadline = time.monotonic() + 3
        blocked = False
        with database_engine.connect() as observer:
            while time.monotonic() < deadline:
                blocked = bool(
                    observer.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name LIKE :name "
                            "AND wait_event_type = 'Lock'"
                            ")"
                        ),
                        {"name": f"{application_prefix}%"},
                    )
                )
                if blocked:
                    break
                time.sleep(0.02)
        assert blocked
        assert not completed.is_set()
    finally:
        release_first.set()
        for worker in workers:
            worker.join(timeout=6)

    assert not errors
    with Session(database_engine) as verify_db:
        decisions = list(
            verify_db.scalars(
                select(AuditLog).where(
                    AuditLog.action == "data_policy.egress.not_served",
                    AuditLog.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    AuditLog.resource_id == str(report_id),
                )
            ).all()
        )
        assert len(decisions) == 1


def test_provider_call_holds_policy_and_receipt_fences_until_settlement(
    database_engine,
    monkeypatch,
    request,
):
    owner_id = uuid.uuid4()
    report_id = uuid.uuid4()
    task_run_id = uuid.uuid4()
    application_prefix = f"tl-ai-fence-{uuid.uuid4().hex[:12]}"
    provider_started = threading.Event()
    release_provider = threading.Event()
    contender_started = {
        name: threading.Event() for name in ("iam", "data", "duplicate", "void")
    }
    worker_threads: list[threading.Thread] = []
    worker_errors: dict[str, BaseException] = {}
    worker_results: dict[str, object] = {}
    provider_calls = 0
    provider_calls_lock = threading.Lock()

    with Session(database_engine) as setup_db:
        state = setup_db.scalar(
            select(DataPolicyState).where(DataPolicyState.id == 1).with_for_update()
        )
        assert state is not None
        original_state = (
            state.mode,
            state.revision,
            state.coverage_version,
            state.enforced_at,
            state.enforced_by_user_id,
            state.updated_by_user_id,
        )
        owner = User(
            id=owner_id,
            email=f"ai-provider-fence-{owner_id}@example.com",
            password_hash="not-used",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
        now = datetime.now(timezone.utc)
        report = Report(
            id=report_id,
            owner_user_id=owner_id,
            title="Provider fence concurrency",
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        task_run = AITaskRun(
            id=task_run_id,
            task_type="report",
            trigger_source="manual",
            status="running",
            report_id=report_id,
            metadata_json={},
        )
        setup_db.add(owner)
        setup_db.flush()
        setup_db.add(report)
        setup_db.flush()
        setup_db.add(task_run)
        state.mode = "audit"
        state.coverage_version = 1
        state.revision += 1
        state.enforced_at = None
        state.enforced_by_user_id = None
        setup_db.flush()
        merge_data_access_envelope_sources(
            setup_db,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
            sources=(
                DataAccessSourceInput(
                    source_type="test_fixture",
                    source_id=str(uuid.uuid4()),
                    source_version="1",
                    handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                    captured_policy_revision=state.revision,
                ),
            ),
        )
        setup_db.commit()

    def cleanup() -> None:
        release_provider.set()
        for worker in worker_threads:
            worker.join(timeout=8)
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AIProviderAttemptReceipt).where(
                    AIProviderAttemptReceipt.task_run_id_snapshot == task_run_id
                )
            )
            cleanup_db.execute(delete(AITaskRun).where(AITaskRun.id == task_run_id))
            cleanup_db.execute(
                delete(AuditLog).where(
                    AuditLog.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    AuditLog.resource_id == str(report_id),
                )
            )
            cleanup_db.execute(
                delete(DataAccessEnvelope).where(
                    DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                    DataAccessEnvelope.resource_id == report_id,
                )
            )
            cleanup_db.execute(delete(Report).where(Report.id == report_id))
            cleanup_db.execute(delete(User).where(User.id == owner_id))
            state = cleanup_db.scalar(
                select(DataPolicyState).where(DataPolicyState.id == 1).with_for_update()
            )
            assert state is not None
            (
                state.mode,
                state.revision,
                state.coverage_version,
                state.enforced_at,
                state.enforced_by_user_id,
                state.updated_by_user_id,
            ) = original_state
            cleanup_db.commit()

    request.addfinalizer(cleanup)
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )
    active = SimpleNamespace(
        request_max_retries=0,
        max_completion_tokens=512,
        provider_type="openai_compatible",
        model="test-model",
    )

    def _set_application_name(db: Session, suffix: str) -> None:
        db.execute(
            text("SELECT set_config('application_name', :name, false)"),
            {"name": f"{application_prefix}-blocked-{suffix}"},
        )

    def _completion() -> AICompletionResult:
        return AICompletionResult(
            payload={"ok": True},
            provider="openai_compatible",
            model="test-model",
            latency_ms=1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    def _run_request(db: Session, provider_call) -> AICompletionResult:
        return run_ai_json_request(
            db,
            active,
            feature_type="report",
            messages=[{"role": "user", "content": "provider fence"}],
            item_id=None,
            daily_brief_id=None,
            report_id=report_id,
            task_run_id=task_run_id,
            provider_operation_scope="section:concurrency",
            max_completion_tokens=None,
            max_retry_completion_tokens=None,
            max_provider_attempts=1,
            execution_checkpoint=None,
            execution_commit=None,
            enforce_egress_data_policy=enforce_ai_egress_data_policy,
            report_feature_type="report",
            call_ai_json=provider_call,
            record_task_run_stop_observed=lambda *_args, **_kwargs: None,
            record_usage_event=lambda *_args, **_kwargs: None,
            build_provider_exchange_payload=lambda **_kwargs: {},
            provider_retry_delay_seconds=lambda **_kwargs: 0,
            ai_error_is_retryable=lambda error: error.retryable,
            next_retry_max_completion_tokens=lambda **kwargs: kwargs["current"],
            sleep=lambda _seconds: None,
        )

    def _provider_call(_active, **_kwargs):
        nonlocal provider_calls
        with provider_calls_lock:
            provider_calls += 1
        provider_started.set()
        assert release_provider.wait(timeout=8)
        return _completion()

    def _unexpected_provider_call(_active, **_kwargs):
        nonlocal provider_calls
        with provider_calls_lock:
            provider_calls += 1
        raise AssertionError("a duplicate worker must not reach provider I/O")

    def _primary_worker() -> None:
        try:
            with Session(database_engine) as worker_db:
                worker_results["primary"] = _run_request(worker_db, _provider_call)
        except BaseException as exc:  # pragma: no cover - surfaced below
            worker_errors["primary"] = exc

    primary = threading.Thread(target=_primary_worker, daemon=True)
    worker_threads.append(primary)
    primary.start()
    assert provider_started.wait(timeout=8)

    with Session(database_engine) as observer_db:
        receipt = observer_db.scalar(
            select(AIProviderAttemptReceipt).where(
                AIProviderAttemptReceipt.task_run_id_snapshot == task_run_id
            )
        )
        assert receipt is not None
        receipt_id = receipt.id
        request_fingerprint = receipt.request_fingerprint
        reservation_generation = receipt.reservation_generation

    def _policy_writer(name: str, model) -> None:
        try:
            with Session(database_engine) as writer_db:
                _set_application_name(writer_db, name)
                contender_started[name].set()
                writer_db.execute(
                    update(model)
                    .where(model.id == 1)
                    .values(revision=model.revision + 1)
                )
                writer_db.rollback()
        except BaseException as exc:  # pragma: no cover - surfaced below
            worker_errors[name] = exc

    def _duplicate_worker() -> None:
        try:
            with Session(database_engine) as duplicate_db:
                _set_application_name(duplicate_db, "duplicate")
                contender_started["duplicate"].set()
                worker_results["duplicate"] = _run_request(
                    duplicate_db, _unexpected_provider_call
                )
        except BaseException as exc:
            worker_errors["duplicate"] = exc

    def _void_worker() -> None:
        try:
            with Session(database_engine) as void_db:
                _set_application_name(void_db, "void")
                contender_started["void"].set()
                void_ai_provider_attempt_reservation(
                    void_db,
                    receipt_id=receipt_id,
                    request_fingerprint=request_fingerprint,
                    reservation_generation=reservation_generation,
                )
                void_db.commit()
        except BaseException as exc:
            worker_errors["void"] = exc

    contenders = [
        threading.Thread(
            target=_policy_writer, args=("iam", IAMPolicyState), daemon=True
        ),
        threading.Thread(
            target=_policy_writer, args=("data", DataPolicyState), daemon=True
        ),
        threading.Thread(target=_duplicate_worker, daemon=True),
        threading.Thread(target=_void_worker, daemon=True),
    ]
    worker_threads.extend(contenders)
    for contender in contenders:
        contender.start()
    for started in contender_started.values():
        assert started.wait(timeout=5)

    expected_blocked_names = {
        f"{application_prefix}-blocked-{name}" for name in contender_started
    }
    deadline = time.monotonic() + 5
    blocked_names: set[str] = set()
    with database_engine.connect() as observer:
        while time.monotonic() < deadline:
            blocked_names = {
                str(row.application_name)
                for row in observer.execute(
                    text(
                        "SELECT application_name FROM pg_stat_activity "
                        "WHERE application_name LIKE :name "
                        "AND wait_event_type = 'Lock'"
                    ),
                    {"name": f"{application_prefix}-blocked-%"},
                )
            }
            if expected_blocked_names <= blocked_names:
                break
            time.sleep(0.02)
    assert expected_blocked_names <= blocked_names
    assert all(contender.is_alive() for contender in contenders)

    release_provider.set()
    for worker in worker_threads:
        worker.join(timeout=8)
    assert not any(worker.is_alive() for worker in worker_threads)
    assert "primary" not in worker_errors
    assert isinstance(worker_results["primary"], AICompletionResult)
    assert isinstance(
        worker_errors.get("duplicate"), AIProviderAttemptReplayBlockedError
    )
    assert isinstance(worker_errors.get("void"), AIProviderAttemptStateError)
    assert "iam" not in worker_errors
    assert "data" not in worker_errors
    assert provider_calls == 1

    with Session(database_engine) as verify_db:
        settled = verify_db.get(AIProviderAttemptReceipt, receipt_id)
        assert settled is not None
        assert settled.state == "succeeded"
        assert settled.io_outcome == "response_received"
        assert settled.reservation_generation == reservation_generation
        assert settled.pre_io_failure_count == 0
