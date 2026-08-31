from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock, Thread
from time import monotonic, sleep

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.alerts import update_alert_interest
from app.core.api_errors import ApiHTTPException
from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceActivity,
    AlertOccurrenceMetric,
)
from app.models.feed import Feed
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.schemas.alert import AlertInterestUpdate
from app.services.alert_evaluation import (
    ALERT_EVALUATION_MAX_ATTEMPTS,
    alert_evaluation_retry_delay,
    claim_alert_evaluation_request,
    evaluate_alert_request,
    persist_alert_backfill_intents,
    persist_alert_evaluation_intent,
    record_alert_evaluation_failure,
    reserve_recoverable_alert_evaluations,
)
from app.services.alert_evaluation_admin import list_alert_evaluation_requests
from app.services.alert_maintenance import maintain_alert_history
from app.services.data_access_policy import DataAccessContext
from app.tasks.alert_tasks import (
    dispatch_pending_alert_evaluations,
    maintain_alert_history_task,
    process_alert_evaluation,
)
from app.tasks.celery_app import (
    QUEUE_MAINTENANCE,
    QUEUE_PROCESSING,
    TASK_ROUTES,
    celery_app,
)


_DISABLED_DATA_ACCESS = DataAccessContext(
    mode="disabled",
    policy_revision=0,
    coverage_version=0,
    principal_type="user",
    principal_id=uuid.UUID(int=0),
    principal_eligible=True,
    allowed_label_ids=frozenset(),
)


def _seed_target(
    db_session,
    user,
    *,
    suffix: str = "one",
    title: str = "Fortinet exploitation observed",
):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name=f"Alert feed {suffix}",
        url=f"https://example.com/{suffix}.xml",
        enabled=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=f"alert-{suffix}",
        url=f"https://example.com/articles/{suffix}",
        canonical_url=f"https://example.com/articles/{suffix}",
        title=title,
        summary="Researchers observed exploitation in the wild.",
        first_seen_at=now,
        dedupe_key=f"alert-{suffix}",
        content_hash=(suffix[0] if suffix else "a") * 64,
        status="content_fetched",
    )
    rule = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"Fortinet watch {suffix}",
        category="appliance",
        keywords=["fortinet"],
        enabled=True,
        severity="high",
        revision=1,
        durable_since=now - timedelta(minutes=5),
    )
    db_session.add_all([feed, item, rule])
    db_session.flush()
    db_session.add(
        ItemClassification(
            item_id=item.id,
            primary_category="vulnerability",
            secondary_categories=[],
            confidence=0.9,
            scores_json={},
            matched_terms_json={},
            source_hash="s" * 64,
            rules_version="test",
        )
    )
    db_session.commit()
    return feed, item, rule


def _run_evaluation(db_session, item: Item, *, now: datetime | None = None):
    intent = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    claim = claim_alert_evaluation_request(
        db_session, request_id=intent.request_id, now=now
    )
    assert claim is not None
    db_session.commit()
    outcome = evaluate_alert_request(
        db_session,
        request_id=intent.request_id,
        lease_token=claim.lease_token,
        now=now,
    )
    db_session.commit()
    return intent, outcome


def test_alert_tasks_are_recoverable_and_routed_to_existing_worker_queues():
    assert process_alert_evaluation.acks_late is True
    assert process_alert_evaluation.reject_on_worker_lost is True
    assert dispatch_pending_alert_evaluations.acks_late is True
    assert maintain_alert_history_task.acks_late is True
    assert maintain_alert_history_task.autoretry_for == (OperationalError,)
    assert maintain_alert_history_task.retry_backoff is True
    assert maintain_alert_history_task.retry_backoff_max == 300
    assert maintain_alert_history_task.retry_jitter is True
    assert maintain_alert_history_task.max_retries == 5
    assert (
        TASK_ROUTES["app.tasks.alert_tasks.process_alert_evaluation"]["queue"]
        == QUEUE_PROCESSING
    )
    assert (
        TASK_ROUTES["app.tasks.alert_tasks.dispatch_pending_alert_evaluations"]["queue"]
        == QUEUE_MAINTENANCE
    )
    assert (
        TASK_ROUTES["app.tasks.alert_tasks.maintain_alert_history"]["queue"]
        == QUEUE_MAINTENANCE
    )
    assert (
        celery_app.conf.beat_schedule["dispatch-pending-alert-evaluations"]["task"]
        == "app.tasks.alert_tasks.dispatch_pending_alert_evaluations"
    )
    assert (
        celery_app.conf.beat_schedule["maintain-alert-history"]["task"]
        == "app.tasks.alert_tasks.maintain_alert_history"
    )
    assert celery_app.conf.beat_schedule["maintain-alert-history"]["schedule"] == 900.0


def test_reconciliation_releases_database_claim_when_publication_fails(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="dispatch-failure",
    )
    intent = persist_alert_evaluation_intent(db_session, item=item)
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    request.dispatch_claimed_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.add(request)
    db_session.commit()

    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr("app.tasks.alert_tasks.db_session", same_session)
    monkeypatch.setattr(
        "app.tasks.alert_tasks.process_alert_evaluation.delay",
        lambda _request_id: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    result = dispatch_pending_alert_evaluations.run()

    assert result == {
        "status": "ok",
        "scanned": 1,
        "queued": 0,
        "enqueue_failed": True,
    }
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.state == "pending"
    assert request.dispatch_claimed_at is None
    assert request.dispatch_failure_count == 1
    assert request.last_dispatch_failed_at is not None
    assert request.last_error_code == "evaluation_dispatch_failed"
    attention = list_alert_evaluation_requests(
        db_session,
        data_access=_DISABLED_DATA_ACCESS,
        states=[],
        sources=[],
        item_id=None,
        page=1,
        page_size=25,
        needs_attention=True,
    )
    assert [row.id for row in attention.items] == [intent.request_id]


def test_attention_includes_only_failed_processing_rows_with_expired_leases(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    request_ids: list[uuid.UUID] = []
    for suffix, lease_expires_at, claimed_at, last_failure in (
        (
            "expired-failed",
            now - timedelta(seconds=1),
            now - timedelta(minutes=2),
            now,
        ),
        (
            "healthy-failed",
            now + timedelta(minutes=2),
            now - timedelta(seconds=5),
            now,
        ),
        (
            "expired-clean",
            now - timedelta(seconds=1),
            now - timedelta(minutes=2),
            None,
        ),
        (
            "expired-historical-failure",
            now - timedelta(seconds=1),
            now - timedelta(minutes=5),
            now - timedelta(minutes=10),
        ),
    ):
        _feed, item, _rule = _seed_target(
            db_session,
            seed_users["viewer"],
            suffix=suffix,
        )
        intent = persist_alert_evaluation_intent(db_session, item=item)
        request = db_session.get(AlertEvaluationRequest, intent.request_id)
        assert request is not None
        request.state = "processing"
        request.lease_token = uuid.uuid4().hex
        request.lease_expires_at = lease_expires_at
        request.claimed_at = claimed_at
        request.dispatch_failure_count = 2 if last_failure else 0
        request.last_dispatch_failed_at = last_failure
        db_session.add(request)
        db_session.commit()
        request_ids.append(request.id)

    attention = list_alert_evaluation_requests(
        db_session,
        data_access=_DISABLED_DATA_ACCESS,
        states=[],
        sources=[],
        item_id=None,
        page=1,
        page_size=25,
        needs_attention=True,
        now=now,
    )

    assert [row.id for row in attention.items] == [request_ids[0]]


def test_alert_evaluation_is_idempotent_and_emits_one_outbox_event(
    db_session,
    seed_users,
):
    feed, item, rule = _seed_target(db_session, seed_users["viewer"])
    feed.site_url = "https://private.example.com/console"
    feed.last_error = "secret upstream diagnostic"
    feed.error_count = 7
    db_session.add(feed)
    db_session.commit()

    first_intent = persist_alert_evaluation_intent(db_session, item=item)
    duplicate_intent = persist_alert_evaluation_intent(db_session, item=item)
    assert first_intent.created is True
    assert duplicate_intent == type(duplicate_intent)(first_intent.request_id, False)
    db_session.commit()

    claim = claim_alert_evaluation_request(
        db_session, request_id=first_intent.request_id
    )
    assert claim is not None
    db_session.commit()
    outcome = evaluate_alert_request(
        db_session,
        request_id=first_intent.request_id,
        lease_token=claim.lease_token,
    )
    db_session.commit()

    assert outcome.occurrences_created == 1
    assert len(outcome.integration_event_ids) == 1
    assert (
        claim_alert_evaluation_request(db_session, request_id=first_intent.request_id)
        is None
    )
    occurrences = list(db_session.scalars(select(AlertOccurrence)).all())
    events = list(
        db_session.scalars(
            select(IntegrationEvent).where(IntegrationEvent.event_type == "alert_match")
        ).all()
    )
    assert len(occurrences) == 1
    assert occurrences[0].alert_interest_id == rule.id
    assert occurrences[0].integration_event_id == events[0].id
    assert len(events) == 1
    assert events[0].payload_json["alert_matches"][0]["owner_user_id"] == str(
        rule.user_id
    )
    assert set(events[0].payload_json["feed"]) == {"id", "name", "url"}
    assert "secret upstream diagnostic" not in str(events[0].payload_json)
    assert "private.example.com" not in str(events[0].payload_json)


def test_concurrent_live_and_backfill_intents_atomically_preserve_live_delivery(
    database_engine,
):
    suffix = uuid.uuid4().hex
    user_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    with Session(database_engine) as setup:
        setup.add(
            User(
                id=user_id,
                email=f"alert-race-{suffix}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup.add(
            Feed(
                id=feed_id,
                name=f"Alert race {suffix}",
                url=f"https://example.com/race-{suffix}.xml",
                enabled=True,
            )
        )
        setup.flush()
        setup.add(
            Item(
                id=item_id,
                feed_id=feed_id,
                source_guid=f"race-{suffix}",
                url=f"https://example.com/race/{suffix}",
                title="Concurrent alert intent",
                dedupe_key=f"race-{suffix}",
                content_hash="r" * 64,
                status="content_fetched",
            )
        )
        setup.commit()

    barrier = Barrier(2)
    lock = Lock()
    results = []
    failures: list[Exception] = []
    session_factory = sessionmaker(
        bind=database_engine,
        class_=Session,
        expire_on_commit=False,
    )

    def worker(source: str) -> None:
        with session_factory() as session:
            try:
                assert session.bind is not None
                assert session.bind.dialect.name == "postgresql"
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                item = session.get(Item, item_id)
                assert item is not None
                barrier.wait(timeout=5)
                result = persist_alert_evaluation_intent(
                    session,
                    item=item,
                    source=source,
                    notify=source == "live",
                    respect_rule_cutover=source == "live",
                )
                session.commit()
                with lock:
                    results.append(result)
            except (
                Exception
            ) as exc:  # pragma: no cover - reported by the assertion below
                session.rollback()
                with lock:
                    failures.append(exc)

    threads = [
        Thread(target=worker, args=("backfill",)),
        Thread(target=worker, args=("live",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert len(results) == 2
        assert len({result.request_id for result in results}) == 1
        assert any(result.created for result in results)
        with Session(database_engine) as verification:
            assert (
                verification.scalar(
                    select(func.count(AlertEvaluationRequest.id)).where(
                        AlertEvaluationRequest.item_id == item_id,
                        AlertEvaluationRequest.item_content_hash == "r" * 64,
                    )
                )
                == 1
            )
            request = verification.scalar(
                select(AlertEvaluationRequest).where(
                    AlertEvaluationRequest.item_id == item_id,
                    AlertEvaluationRequest.item_content_hash == "r" * 64,
                )
            )
            assert request is not None
            assert request.source == "live"
            assert request.active_source == "live"
            assert request.notify is True
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(AlertEvaluationRequest).where(
                    AlertEvaluationRequest.item_id == item_id
                )
            )
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.commit()


def test_unrelated_item_acceptance_is_not_globally_serialized(
    database_engine,
):
    user_id = uuid.uuid4()
    with Session(database_engine) as setup:
        user = User(
            id=user_id,
            email=f"alert-lock-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
        setup.add(user)
        setup.commit()
        feed_a, item_a, _rule_a = _seed_target(
            setup, user, suffix=f"lock-a-{uuid.uuid4().hex}"
        )
        feed_b, item_b, _rule_b = _seed_target(
            setup, user, suffix=f"lock-b-{uuid.uuid4().hex}"
        )
        feed_ids = (feed_a.id, feed_b.id)
        item_ids = (item_a.id, item_b.id)
    blocker_ready = Event()
    release_blocker = Event()
    item_b_done = Event()
    failures: list[Exception] = []
    session_factory = sessionmaker(bind=database_engine, class_=Session)

    def hold_item_a() -> None:
        with session_factory() as session:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.scalar(select(Item).where(Item.id == item_a.id).with_for_update())
            blocker_ready.set()
            release_blocker.wait(timeout=5)
            session.rollback()

    def accept(item_id: uuid.UUID, completed: Event | None = None) -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                item = session.get(Item, item_id)
                assert item is not None
                persist_alert_evaluation_intent(session, item=item)
                session.commit()
                if completed is not None:
                    completed.set()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

    blocker = Thread(target=hold_item_a)
    blocked = Thread(target=accept, args=(item_a.id,))
    unrelated = Thread(target=accept, args=(item_b.id, item_b_done))
    try:
        blocker.start()
        assert blocker_ready.wait(timeout=3)
        blocked.start()
        unrelated.start()
        assert item_b_done.wait(timeout=3)
    finally:
        release_blocker.set()
        for thread in (blocker, blocked, unrelated):
            if thread.ident is not None:
                thread.join(timeout=5)
    try:
        assert not failures
        assert all(not thread.is_alive() for thread in (blocker, blocked, unrelated))
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(AlertEvaluationRequest).where(
                    AlertEvaluationRequest.item_id.in_(item_ids)
                )
            )
            cleanup.execute(delete(Feed).where(Feed.id.in_(feed_ids)))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_legacy_concurrent_rule_updates_receive_distinct_revisions(database_engine):
    user_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    initial_cutover = datetime.now(timezone.utc) - timedelta(minutes=1)
    with Session(database_engine) as setup:
        setup.add(
            User(
                id=user_id,
                email=f"legacy-rule-race-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup.commit()
        setup.add(
            AlertInterest(
                id=rule_id,
                user_id=user_id,
                name="Legacy rule",
                category="threat",
                keywords=["legacy"],
                enabled=True,
                severity="medium",
                revision=1,
                durable_since=initial_cutover,
            )
        )
        setup.commit()

    barrier = Barrier(2)
    lock = Lock()
    versions: list[tuple[int, int]] = []
    failures: list[Exception] = []
    session_factory = sessionmaker(bind=database_engine, class_=Session)

    def legacy_update(label: str) -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                barrier.wait(timeout=5)
                version = session.execute(
                    text(
                        "UPDATE alert_interests SET name = :name WHERE id = :id "
                        "RETURNING revision, row_version"
                    ),
                    {"id": rule_id, "name": f"Legacy {label}"},
                ).one()
                session.commit()
                with lock:
                    versions.append((int(version.revision), int(version.row_version)))
            except Exception as exc:  # pragma: no cover - asserted below
                session.rollback()
                with lock:
                    failures.append(exc)

    threads = [Thread(target=legacy_update, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(versions) == [(2, 2), (3, 3)]
        with Session(database_engine) as verification:
            rule = verification.get(AlertInterest, rule_id)
            assert rule is not None
            assert rule.revision == 3
            assert rule.row_version == 3
            assert rule.durable_since is not None
            assert rule.durable_since > initial_cutover
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(delete(AlertInterest).where(AlertInterest.id == rule_id))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_concurrent_rule_patch_mutations_are_serialized(database_engine):
    user_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    with Session(database_engine) as setup:
        setup.add(
            User(
                id=user_id,
                email=f"rule-patch-race-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup.commit()
        setup.add(
            AlertInterest(
                id=rule_id,
                user_id=user_id,
                name="Patch race",
                category="threat",
                keywords=["patch"],
                enabled=True,
                severity="medium",
                revision=1,
                durable_since=datetime.now(timezone.utc),
            )
        )
        setup.commit()

    barrier = Barrier(2)
    lock = Lock()
    versions: list[tuple[int, int]] = []
    failures: list[Exception] = []
    session_factory = sessionmaker(bind=database_engine, class_=Session)

    def patch_rule(label: str) -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                user = session.get(User, user_id)
                assert user is not None
                barrier.wait(timeout=5)
                updated = update_alert_interest(
                    rule_id,
                    AlertInterestUpdate(name=f"Patch {label}"),
                    session,
                    user,
                )
                with lock:
                    versions.append((updated.revision, updated.row_version))
            except Exception as exc:  # pragma: no cover - asserted below
                session.rollback()
                with lock:
                    failures.append(exc)

    threads = [Thread(target=patch_rule, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(versions) == [(2, 2), (3, 3)]
        with Session(database_engine) as verification:
            rule = verification.get(AlertInterest, rule_id)
            assert rule.revision == 3
            assert rule.row_version == 3
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(delete(AlertInterest).where(AlertInterest.id == rule_id))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_concurrent_suppression_and_disable_share_one_optimistic_row_version(
    database_engine,
):
    user_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    initial_cutover = datetime.now(timezone.utc) - timedelta(minutes=5)
    with Session(database_engine) as setup:
        setup.add(
            User(
                id=user_id,
                email=f"rule-state-race-{uuid.uuid4().hex}@example.com",
                password_hash="x",
                role="viewer",
                is_active=True,
                is_approved=True,
            )
        )
        setup.commit()
        setup.add(
            AlertInterest(
                id=rule_id,
                user_id=user_id,
                name="State race",
                category="threat",
                keywords=["race"],
                enabled=True,
                severity="medium",
                revision=1,
                row_version=1,
                durable_since=initial_cutover,
            )
        )
        setup.commit()

    barrier = Barrier(2)
    lock = Lock()
    successes: list[tuple[str, int, int]] = []
    conflicts: list[tuple[str, ApiHTTPException]] = []
    failures: list[Exception] = []
    session_factory = sessionmaker(bind=database_engine, class_=Session)

    def mutate_rule(operation: str) -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                user = session.get(User, user_id)
                assert user is not None
                payload = (
                    AlertInterestUpdate(
                        suppression_until=datetime.now(timezone.utc)
                        + timedelta(hours=2),
                        suppression_reason="Concurrent maintenance",
                        expected_revision=1,
                    )
                    if operation == "suppress"
                    else AlertInterestUpdate(enabled=False, expected_row_version=1)
                )
                barrier.wait(timeout=5)
                updated = update_alert_interest(rule_id, payload, session, user)
                with lock:
                    successes.append((operation, updated.revision, updated.row_version))
            except ApiHTTPException as exc:
                session.rollback()
                with lock:
                    conflicts.append((operation, exc))
            except Exception as exc:  # pragma: no cover - asserted below
                session.rollback()
                with lock:
                    failures.append(exc)

    threads = [
        Thread(target=mutate_rule, args=(operation,))
        for operation in ("suppress", "disable")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert len(successes) == 1
        assert successes[0][1:] == (1, 2)
        assert len(conflicts) == 1
        assert conflicts[0][1].status_code == 409
        assert conflicts[0][1].error_code == "alert_revision_conflict"
        assert conflicts[0][1].detail["current_row_version"] == 2

        with Session(database_engine) as verification:
            rule = verification.get(AlertInterest, rule_id)
            assert rule is not None
            assert rule.revision == 1
            assert rule.row_version == 2
            if successes[0][0] == "disable":
                assert rule.enabled is False
                assert rule.durable_since is None
                assert rule.suppression_until is None
            else:
                assert rule.enabled is True
                assert rule.durable_since == initial_cutover
                assert rule.suppression_until is not None

        with Session(database_engine) as no_op_session:
            user = no_op_session.get(User, user_id)
            rule = no_op_session.get(AlertInterest, rule_id)
            assert user is not None and rule is not None
            unchanged = update_alert_interest(
                rule_id,
                AlertInterestUpdate(
                    enabled=rule.enabled,
                    expected_row_version=rule.row_version,
                ),
                no_op_session,
                user,
            )
            assert unchanged.revision == 1
            assert unchanged.row_version == 2
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(delete(AlertInterest).where(AlertInterest.id == rule_id))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_acceptance_and_evaluation_share_item_then_request_lock_order(
    database_engine,
):
    user_id = uuid.uuid4()
    with Session(database_engine) as setup:
        user = User(
            id=user_id,
            email=f"alert-deadlock-{uuid.uuid4().hex}@example.com",
            password_hash="x",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
        setup.add(user)
        setup.commit()
        feed, item, _rule = _seed_target(
            setup,
            user,
            suffix=f"deadlock-{uuid.uuid4().hex}",
        )
        feed_id = feed.id
        item_id = item.id
        intent = persist_alert_evaluation_intent(setup, item=item)
        setup.commit()
        claim = claim_alert_evaluation_request(setup, request_id=intent.request_id)
        assert claim is not None
        setup.commit()

    evaluator_ready = Event()
    evaluator_pid: list[int] = []
    evaluator_failures: list[Exception] = []
    session_factory = sessionmaker(bind=database_engine, class_=Session)

    def evaluate() -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                evaluator_pid.append(
                    int(session.scalar(text("SELECT pg_backend_pid()")))
                )
                evaluator_ready.set()
                evaluate_alert_request(
                    session,
                    request_id=intent.request_id,
                    lease_token=claim.lease_token,
                )
                session.commit()
            except Exception as exc:  # pragma: no cover - asserted below
                session.rollback()
                evaluator_failures.append(exc)

    evaluator = Thread(target=evaluate)
    with session_factory() as acceptance:
        acceptance.execute(text("SET LOCAL lock_timeout = '5s'"))
        locked_item = acceptance.scalar(
            select(Item).where(Item.id == item_id).with_for_update()
        )
        assert locked_item is not None
        evaluator.start()
        assert evaluator_ready.wait(timeout=3)
        deadline = monotonic() + 3
        while monotonic() < deadline:
            wait_type = acceptance.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": evaluator_pid[0]},
            )
            if wait_type == "Lock":
                break
            sleep(0.05)
        assert wait_type == "Lock"
        duplicate = persist_alert_evaluation_intent(acceptance, item=locked_item)
        assert duplicate.request_id == intent.request_id
        assert duplicate.created is False
        acceptance.commit()

    evaluator.join(timeout=10)
    try:
        assert not evaluator.is_alive()
        assert not evaluator_failures
        with Session(database_engine) as verification:
            request = verification.get(AlertEvaluationRequest, intent.request_id)
            assert request is not None
            assert request.state == "succeeded"
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(IntegrationEvent).where(
                    IntegrationEvent.source_id == str(item_id)
                )
            )
            cleanup.execute(
                delete(AlertEvaluationRequest).where(
                    AlertEvaluationRequest.id == intent.request_id
                )
            )
            cleanup.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


@pytest.mark.parametrize(
    ("attempt", "low", "high"),
    [(1, 24.0, 36.0), (2, 48.0, 72.0), (20, 2_880.0, 3_600.0)],
)
def test_alert_retry_jitter_has_deterministic_bounded_edges(attempt, low, high):
    assert alert_evaluation_retry_delay(attempt, jitter_fraction=0) == low
    assert alert_evaluation_retry_delay(attempt, jitter_fraction=1) == high


def test_live_intent_promotes_completed_backfill_occurrence_into_one_event(
    db_session,
    seed_users,
):
    _feed, item, _rule = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="backfill-live-promotion",
    )
    backfill = persist_alert_evaluation_intent(
        db_session,
        item=item,
        source="backfill",
        notify=False,
        respect_rule_cutover=False,
    )
    db_session.commit()
    claim = claim_alert_evaluation_request(
        db_session,
        request_id=backfill.request_id,
    )
    assert claim is not None
    db_session.commit()
    backfill_outcome = evaluate_alert_request(
        db_session,
        request_id=backfill.request_id,
        lease_token=claim.lease_token,
    )
    db_session.commit()
    assert backfill_outcome.occurrences_created == 1
    assert backfill_outcome.integration_event_ids == ()

    live = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    assert live.request_id == backfill.request_id
    assert live.created is True
    claim = claim_alert_evaluation_request(db_session, request_id=live.request_id)
    assert claim is not None
    db_session.commit()
    live_outcome = evaluate_alert_request(
        db_session,
        request_id=live.request_id,
        lease_token=claim.lease_token,
    )
    db_session.commit()

    assert live_outcome.occurrences_created == 0
    assert len(live_outcome.integration_event_ids) == 1
    assert db_session.scalar(select(func.count(AlertOccurrence.id))) == 1
    assert (
        db_session.scalar(
            select(func.count(IntegrationEvent.id)).where(
                IntegrationEvent.event_type == "alert_match"
            )
        )
        == 1
    )
    request = db_session.get(AlertEvaluationRequest, live.request_id)
    assert request.source == "live"
    assert request.notify_existing_occurrences is False


def test_suppression_is_auditable_but_not_notification_deduplication(
    db_session,
    seed_users,
):
    _feed, item, suppressed_rule = _seed_target(
        db_session, seed_users["viewer"], suffix="suppressed"
    )
    suppressed_rule.suppression_until = datetime.now(timezone.utc) + timedelta(hours=2)
    suppressed_rule.suppression_reason = "Maintenance window"
    deliverable_rule = AlertInterest(
        id=uuid.uuid4(),
        user_id=seed_users["viewer"].id,
        name="Second matching rule",
        category="campaign",
        keywords=["exploitation"],
        enabled=True,
        severity="critical",
        revision=1,
        durable_since=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add_all([suppressed_rule, deliverable_rule])
    db_session.commit()

    _intent, outcome = _run_evaluation(db_session, item)
    occurrences = list(
        db_session.scalars(
            select(AlertOccurrence).order_by(AlertOccurrence.alert_name_snapshot)
        ).all()
    )
    event = db_session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.event_type == "alert_match")
    )

    assert outcome.occurrences_created == 2
    assert outcome.suppressed_occurrences == 1
    assert len(occurrences) == 2
    suppressed = next(
        row for row in occurrences if row.alert_interest_id == suppressed_rule.id
    )
    assert suppressed.is_suppressed is True
    assert suppressed.suppression_reason == "Maintenance window"
    assert suppressed.integration_event_id is None
    suppressed_activity = db_session.scalar(
        select(AlertOccurrenceActivity).where(
            AlertOccurrenceActivity.occurrence_id == suppressed.id
        )
    )
    assert suppressed_activity is not None
    assert (
        suppressed_activity.details_json["suppression_reason"] == "Maintenance window"
    )
    assert event is not None
    assert event.payload_json["alert_matches"][0]["names"] == ["Second matching rule"]
    assert db_session.scalar(select(func.count(AlertOccurrence.id))) == 2
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 1


def test_live_reprocessing_does_not_implicitly_backfill_pre_rule_items(
    db_session,
    seed_users,
):
    _feed, item, rule = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="pre-cutover",
    )
    item.first_seen_at = datetime.now(timezone.utc) - timedelta(days=30)
    rule.durable_since = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add_all([item, rule])
    db_session.commit()

    intent, outcome = _run_evaluation(db_session, item)

    assert outcome.evaluated_rules == 0
    assert outcome.occurrences_created == 0
    assert outcome.integration_event_ids == ()
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.accepted_at >= rule.durable_since
    assert request.respect_rule_cutover is True
    assert db_session.scalar(select(func.count(AlertOccurrence.id))) == 0
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 0


def test_outbox_failure_rolls_back_occurrence_creation(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="atomic",
    )
    intent = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    claim = claim_alert_evaluation_request(db_session, request_id=intent.request_id)
    assert claim is not None
    db_session.commit()
    monkeypatch.setattr(
        "app.services.alert_evaluation_execution.emit_integration_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("outbox unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        evaluate_alert_request(
            db_session,
            request_id=intent.request_id,
            lease_token=claim.lease_token,
        )
    db_session.rollback()

    assert db_session.scalar(select(func.count(AlertOccurrence.id))) == 0
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 0
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.state == "processing"
    assert request.lease_token == claim.lease_token


def test_stale_lease_is_recovered_and_persisted_errors_are_redacted(
    db_session,
    seed_users,
):
    _feed, item, _rule = _seed_target(
        db_session, seed_users["viewer"], suffix="recovery"
    )
    intent = persist_alert_evaluation_intent(db_session, item=item)
    now = datetime.now(timezone.utc)
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    request.state = "processing"
    request.attempt_count = 1
    request.claimed_at = now - timedelta(minutes=5)
    request.lease_token = "expired-token"
    request.lease_expires_at = now - timedelta(seconds=1)
    request.dispatch_claimed_at = None
    db_session.add(request)
    db_session.commit()

    reservation = reserve_recoverable_alert_evaluations(
        db_session, now=now, batch_size=10
    )
    assert reservation.request_ids == (intent.request_id,)
    db_session.commit()
    claim = claim_alert_evaluation_request(
        db_session, request_id=intent.request_id, now=now
    )
    assert claim is not None
    assert claim.lease_token != "expired-token"
    assert claim.attempt_number == 2
    db_session.commit()

    failure = record_alert_evaluation_failure(
        db_session,
        request_id=intent.request_id,
        lease_token=claim.lease_token,
        error=RuntimeError("SECRET article body and smtp password"),
        now=now,
    )
    db_session.commit()
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert failure is not None
    assert failure.state == "retry_wait"
    assert failure.retry_at is not None
    retry_delay = (failure.retry_at - now).total_seconds()
    assert 48 <= retry_delay <= 72
    assert request.last_error_code == "evaluation_worker_error"
    assert "SECRET" not in request.last_error_message
    assert "password" not in request.last_error_message

    request.state = "processing"
    request.lease_token = "final-token"
    request.attempt_count = ALERT_EVALUATION_MAX_ATTEMPTS
    db_session.add(request)
    db_session.commit()
    terminal = record_alert_evaluation_failure(
        db_session,
        request_id=intent.request_id,
        lease_token="final-token",
        error=RuntimeError("still secret"),
        now=now,
    )
    db_session.commit()
    assert terminal is not None
    assert terminal.state == "dead_letter"


def test_rule_revision_and_explicit_backfill_create_distinct_non_notifying_history(
    db_session,
    seed_users,
):
    _feed, item, rule = _seed_target(
        db_session, seed_users["viewer"], suffix="revision"
    )
    _first_intent, first = _run_evaluation(db_session, item)
    assert first.occurrences_created == 1

    rule.name = f"{rule.name} revised"
    rule.revision = 2
    rule.row_version = 2
    rule.durable_since = datetime.now(timezone.utc)
    item.content_hash = "z" * 64
    db_session.add_all([rule, item])
    db_session.commit()
    live_intent, live_result = _run_evaluation(db_session, item)
    assert live_result.evaluated_rules == 0
    assert live_result.occurrences_created == 0
    assert live_result.integration_event_ids == ()

    backfill = persist_alert_backfill_intents(
        db_session,
        data_access=_DISABLED_DATA_ACCESS,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()
    assert backfill.request_ids == (live_intent.request_id,)
    claim = claim_alert_evaluation_request(
        db_session, request_id=live_intent.request_id
    )
    assert claim is not None
    db_session.commit()
    second = evaluate_alert_request(
        db_session,
        request_id=live_intent.request_id,
        lease_token=claim.lease_token,
    )
    db_session.commit()

    assert second.occurrences_created == 1
    assert second.integration_event_ids == ()
    revisions = list(
        db_session.scalars(
            select(AlertOccurrence.rule_revision).order_by(
                AlertOccurrence.rule_revision
            )
        ).all()
    )
    assert revisions == [1, 2]
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 1


def test_alert_history_retention_aggregates_closed_and_never_deletes_open(
    db_session,
    seed_users,
):
    _feed, item, rule = _seed_target(
        db_session, seed_users["viewer"], suffix="retention"
    )
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    closed = AlertOccurrence(
        id=uuid.uuid4(),
        alert_interest_id=rule.id,
        rule_id_snapshot=rule.id,
        owner_user_id=rule.user_id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=1,
        item_content_hash=item.content_hash,
        alert_name_snapshot=rule.name,
        alert_category_snapshot=rule.category,
        alert_keywords_snapshot=rule.keywords,
        matched_keywords=["fortinet"],
        source_snapshot_json={},
        severity_snapshot="high",
        lifecycle_state="closed",
        closure_disposition="true_positive",
        closed_at=old,
        created_at=old,
        updated_at=old,
    )
    open_occurrence = AlertOccurrence(
        id=uuid.uuid4(),
        alert_interest_id=rule.id,
        rule_id_snapshot=rule.id,
        owner_user_id=rule.user_id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=2,
        item_content_hash="o" * 64,
        alert_name_snapshot=rule.name,
        alert_category_snapshot=rule.category,
        alert_keywords_snapshot=rule.keywords,
        matched_keywords=["fortinet"],
        source_snapshot_json={},
        severity_snapshot="high",
        lifecycle_state="new",
        created_at=old,
        updated_at=old,
    )
    evaluation = AlertEvaluationRequest(
        id=uuid.uuid4(),
        item_id=item.id,
        item_content_hash="e" * 64,
        state="succeeded",
        completed_at=old,
        created_at=old,
        updated_at=old,
    )
    db_session.add_all([closed, open_occurrence, evaluation])
    db_session.flush()
    closed_id = closed.id
    open_occurrence_id = open_occurrence.id
    db_session.add_all(
        [
            AlertOccurrenceActivity(
                occurrence_id=open_occurrence_id,
                action="created",
                details_json={},
                created_at=old,
            ),
            AlertOccurrenceActivity(
                occurrence_id=open_occurrence_id,
                action="snoozed",
                details_json={},
                created_at=old,
            ),
        ]
    )
    db_session.commit()

    result = maintain_alert_history(
        db_session,
        now=now,
        occurrence_retention_days=1,
        activity_retention_days=1,
        evaluation_retention_days=1,
        metric_retention_days=30,
    )

    assert result.occurrences_aggregated == 1
    assert result.occurrences_deleted == 1
    assert result.activities_deleted == 1
    assert result.evaluations_deleted == 1
    assert db_session.get(AlertOccurrence, closed_id) is None
    assert db_session.get(AlertOccurrence, open_occurrence_id) is not None
    metric = db_session.scalar(select(AlertOccurrenceMetric))
    assert metric is not None
    assert metric.occurrence_count == 1
    remaining_actions = list(
        db_session.scalars(
            select(AlertOccurrenceActivity.action).where(
                AlertOccurrenceActivity.occurrence_id == open_occurrence_id
            )
        ).all()
    )
    assert remaining_actions == ["created"]


def test_alert_history_maintenance_repeats_bounded_batches_and_reports_backlog(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    expired_at = now - timedelta(minutes=1)
    previews = [
        AlertBackfillPreview(
            actor_user_id=seed_users["admin"].id,
            since=now - timedelta(days=2),
            until=now - timedelta(days=1),
            item_limit=10,
            candidates_json=[],
            matched_count=0,
            has_more=False,
            expires_at=expired_at,
        )
        for _ in range(5)
    ]
    db_session.add_all(previews)
    db_session.commit()

    limited = maintain_alert_history(
        db_session,
        now=now,
        batch_size=2,
        max_batches=1,
        max_runtime_seconds=10,
    )

    assert limited.previews_deleted == 2
    assert limited.batches_processed == 1
    assert limited.stop_reason == "batch_limit"
    assert limited.backlog_remaining is True
    assert limited.backlog_categories == ("expired_previews",)

    drained = maintain_alert_history(
        db_session,
        now=now,
        batch_size=2,
        max_batches=10,
        max_runtime_seconds=10,
    )

    assert drained.previews_deleted == 3
    assert drained.batches_processed >= 2
    assert drained.stop_reason == "drained"
    assert drained.backlog_remaining is False
    assert drained.backlog_categories == ()


def test_alert_history_maintenance_stops_at_runtime_cap_with_visible_backlog(
    db_session,
    seed_users,
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            AlertBackfillPreview(
                actor_user_id=seed_users["admin"].id,
                since=now - timedelta(days=2),
                until=now - timedelta(days=1),
                item_limit=10,
                candidates_json=[],
                matched_count=0,
                has_more=False,
                expires_at=now - timedelta(minutes=1),
            )
            for _ in range(2)
        ]
    )
    db_session.commit()
    clock_values = iter((0.0, 1.0, 1.0))

    result = maintain_alert_history(
        db_session,
        now=now,
        batch_size=1,
        max_batches=10,
        max_runtime_seconds=0.5,
        _clock=lambda: next(clock_values),
    )

    assert result.previews_deleted == 1
    assert result.batches_processed == 1
    assert result.stop_reason == "runtime_limit"
    assert result.backlog_remaining is True
    assert result.backlog_categories == ("expired_previews",)
