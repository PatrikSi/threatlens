"""Real PostgreSQL recovery transitions with synthetic sources and credentials."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.api_token import ApiToken
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import (
    ProcessingRecoveryItem,
    ProcessingRecoveryRun,
    ProcessingWork,
)
from app.models.user import User
from app.services import processing_worker as worker
from app.services.processing_dispatch import (
    discover_processing_work,
    maintain_processing_work,
    prepare_processing_publications,
)
from app.services.classification_recovery import require_item_classification
from tests.integration.test_export_jobs import export_env as export_env


@pytest.fixture
def processing_env(export_env, monkeypatch):
    env = export_env
    with Session(env.engine) as db:
        db.get(User, env.owner_id).role = "admin"
        db.get(ApiToken, env.credential_id).scopes = [
            "read:items",
            "read:operations",
            "write:operations",
        ]
        db.commit()
    # Suppress outbound alert queue publication while retaining its durable intent.
    monkeypatch.setattr(
        "app.tasks.alert_tasks.enqueue_alert_evaluation_requests", lambda _ids: True
    )
    yield env
    with Session(env.engine) as db:
        db.execute(
            delete(ProcessingRecoveryRun).where(
                ProcessingRecoveryRun.principal_id == env.owner_id
            )
        )
        db.commit()


def _selection(env, stage="classification"):
    response = env.client.get(
        "/processing/work",
        params={"feed_id": str(env.feed_id), "stage": stage},
        headers=env.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def _accept(env, stage="classification"):
    selection = _selection(env, stage)
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "items": [{key: selection[key] for key in ("item_id", "stage", "revision")}],
    }
    response = env.client.post(
        "/processing/recovery-runs", json=payload, headers=env.headers
    )
    assert response.status_code == 202, response.text
    return response.json(), payload


def _publication(env):
    with Session(env.engine) as db:
        discover_processing_work(db, stage="classification")
        db.commit()
    with Session(env.engine) as db:
        values = prepare_processing_publications(
            db, canary_at=None, stage="classification"
        )
        db.commit()
    assert len(values) == 1
    return values[0]


def _status(env, run_id):
    response = env.client.get(
        f"/processing/recovery-runs/{run_id}", headers=env.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_acceptance_is_idempotent_and_completion_acknowledges_domain_atomically(
    processing_env,
):
    env = processing_env
    run, payload = _accept(env)
    repeat = env.client.post(
        "/processing/recovery-runs", json=payload, headers=env.headers
    )
    assert repeat.status_code == 202 and repeat.json()["id"] == run["id"]
    identity, token = _publication(env)
    assert worker.execute_processing_work(identity, token)["status"] == "succeeded"
    assert worker.execute_processing_work(identity, token)["reason"] == "stale_claim"
    status = _status(env, run["id"])
    assert status["status"] == "succeeded" and status["completed_count"] == 1
    assert status["items"][0]["title"] == "Export source evidence"
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        assert (
            item.classification_completed_version
            == item.classification_required_version
        )
        assert db.get(ItemClassification, env.item_id) is not None


def test_cancelled_message_cannot_begin_work(processing_env):
    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    current = _status(env, run["id"])
    cancelled = env.client.post(
        f"/processing/recovery-runs/{run['id']}/cancel",
        json={"expected_version": current["version"]},
        headers=env.headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert worker.execute_processing_work(identity, token)["reason"] == "stale_claim"
    assert cancelled.json()["cancelled_count"] == 1
    with Session(env.engine) as db:
        assert db.get(ItemClassification, env.item_id) is None


def test_cancel_during_domain_attempt_rolls_back_results_without_waiting_for_worker(
    processing_env, monkeypatch
):
    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    reached, release = Event(), Event()
    original = worker._settle_attempt

    def pause(db, work):
        reached.set()
        assert release.wait(10)
        original(db, work)

    monkeypatch.setattr(worker, "_settle_attempt", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.execute_processing_work, identity, token)
        assert reached.wait(10)
        current = _status(env, run["id"])
        response = env.client.post(
            f"/processing/recovery-runs/{run['id']}/cancel",
            json={"expected_version": current["version"]},
            headers=env.headers,
        )
        release.set()
        assert response.status_code == 200, response.text
        assert future.result(timeout=10)["status"] == "cancelled"
    with Session(env.engine) as db:
        assert db.get(ItemClassification, env.item_id) is None
    assert _status(env, run["id"])["cancelled_count"] == 1


def test_refresh_after_acceptance_rejects_stale_source_without_acknowledgement(
    processing_env,
):
    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        require_item_classification(item)
        item.title = "New source revision"
        db.commit()
    assert worker.execute_processing_work(identity, token)["status"] == "attention"
    assert _status(env, run["id"])["items"][0]["reason"] == "source_changed"
    with Session(env.engine) as db:
        assert db.get(ItemClassification, env.item_id) is None
        assert discover_processing_work(db, stage="classification") == 1
        db.commit()


def test_expired_running_claim_recovers_with_delay_and_old_token_cannot_finish(
    processing_env,
):
    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    assert worker.claim_processing_work(identity, token)
    with Session(env.engine) as db:
        db.get(ProcessingWork, identity).lease_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        db.commit()
    with Session(env.engine) as db:
        assert maintain_processing_work(db) == 1
        work = db.get(ProcessingWork, identity)
        assert work.status == "retry_wait" and work.next_retry_at > datetime.now(
            timezone.utc
        )
        assert prepare_processing_publications(db, canary_at=None) == []
        work.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    replacement_id, replacement = _publication(env)
    assert replacement != token
    assert worker.execute_processing_work(identity, token)["reason"] == "stale_claim"
    assert (
        worker.execute_processing_work(replacement_id, replacement)["status"]
        == "succeeded"
    )
    assert _status(env, run["id"])["completed_count"] == 1


def test_paused_consumer_does_not_accumulate_republished_messages(processing_env):
    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        heartbeat = work.published_canary_at
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    for _ in range(10):
        with Session(env.engine) as db:
            assert prepare_processing_publications(db, canary_at=heartbeat) == []
            db.commit()
    with Session(env.engine) as db:
        replacement = prepare_processing_publications(
            db, canary_at=heartbeat + timedelta(seconds=1)
        )
        assert len(replacement) == 1 and replacement[0][1] != token
        db.commit()


def test_scope_reduction_of_accepting_token_stops_existing_run(processing_env):
    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    with Session(env.engine) as db:
        db.get(ApiToken, env.credential_id).scopes = ["read:items", "read:operations"]
        db.commit()
    assert worker.execute_processing_work(identity, token)["status"] == "attention"
    status = _status(env, run["id"])
    assert (
        status["access_limited"]
        and status["items"] == []
        and status["total_count"] == 0
    )


def test_missing_classification_row_after_prior_success_is_discoverable_again(
    processing_env,
):
    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    assert worker.execute_processing_work(identity, token)["status"] == "succeeded"
    with Session(env.engine) as db:
        # A newly missing committed result is a new obligation, even when its
        # previous successful generation consumed the whole retry allowance.
        db.get(ProcessingWork, identity).attempts = 5
        db.execute(
            delete(ItemClassification).where(ItemClassification.item_id == env.item_id)
        )
        db.commit()
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage="classification") == 1
        assert db.get(ProcessingWork, identity).generation == 2
        db.commit()


@pytest.mark.parametrize("crashes", [1, 2, 4, 5])
def test_generated_crash_sequences_never_commit_partial_domain_results(
    processing_env, monkeypatch, crashes
):
    env = processing_env
    run, _ = _accept(env)
    original = worker._settle_attempt
    failed = 0

    def crash_before_commit(db, work):
        nonlocal failed
        if failed < crashes:
            failed += 1
            raise RuntimeError("simulated process failure after domain flush")
        original(db, work)

    monkeypatch.setattr(worker, "_settle_attempt", crash_before_commit)
    for attempt in range(min(crashes + 1, 5)):
        identity, token = _publication(env)
        result = worker.execute_processing_work(identity, token)
        with Session(env.engine) as db:
            work = db.get(ProcessingWork, identity)
            if attempt < crashes:
                assert db.get(ItemClassification, env.item_id) is None
                assert work.attempts == attempt + 1
                assert result["status"] == (
                    "attention" if attempt == 4 else "retry_wait"
                )
                work.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
    status = _status(env, run["id"])
    assert status["status"] == ("failed" if crashes == 5 else "succeeded")
    assert status["completed_count"] == int(crashes < 5)


def test_selected_batch_larger_than_feed_admission_resumes_in_bounded_groups(
    processing_env, monkeypatch
):
    from app.core.config import get_settings
    from app.models.article import Article
    from app.services.feed_pipeline import upsert_item_from_parsed
    from app.models.feed import Feed
    from types import SimpleNamespace

    env = processing_env
    monkeypatch.setattr(get_settings(), "processing_dispatch_per_feed", 2)
    with Session(env.engine) as db:
        feed = db.get(Feed, env.feed_id)
        for index in range(6):
            key = uuid.uuid4().hex
            item, _, _ = upsert_item_from_parsed(
                db,
                feed,
                SimpleNamespace(
                    url=f"https://example.invalid/{key}",
                    guid=key,
                    title=f"Recovery source {index}",
                    summary="Synthetic",
                    published_at=None,
                ),
            )
            db.add(
                Article(
                    item_id=item.id,
                    final_url=item.url,
                    http_status=200,
                    text="Synthetic evidence",
                )
            )
        db.commit()
    found = env.client.get(
        "/processing/work",
        params={"feed_id": str(env.feed_id), "stage": "classification"},
        headers=env.headers,
    ).json()["items"]
    response = env.client.post(
        "/processing/recovery-runs",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [
                {key: row[key] for key in ("item_id", "stage", "revision")}
                for row in found
            ],
        },
        headers=env.headers,
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["total_count"] == 7
    counts = []
    for _ in range(4):
        with Session(env.engine) as db:
            discover_processing_work(db, stage="classification")
            db.commit()
        with Session(env.engine) as db:
            # Repeated discovery cannot fill reservations beyond this feed's cap.
            assert discover_processing_work(db, stage="classification") == 0
            publications = prepare_processing_publications(
                db, canary_at=None, stage="classification"
            )
            db.commit()
        counts.append(len(publications))
        for identity, token in publications:
            assert (
                worker.execute_processing_work(identity, token)["status"] == "succeeded"
            )
    assert counts == [2, 2, 2, 1]
    assert _status(env, run["id"])["completed_count"] == 7


def test_terminal_history_expiry_removes_ledger_without_replaying_completed_work(
    processing_env,
):
    from app.services.processing_dispatch import prune_recovery_history

    env = processing_env
    run, payload = _accept(env)
    identity, token = _publication(env)
    worker.execute_processing_work(identity, token)
    with Session(env.engine) as db:
        db.get(ProcessingRecoveryRun, uuid.UUID(run["id"])).updated_at = datetime.now(
            timezone.utc
        ) - timedelta(days=8)
        db.commit()
    with Session(env.engine) as db:
        assert prune_recovery_history(db) == 1
        db.commit()
        assert db.get(ProcessingWork, identity).recovery_run_id is None
        assert (
            db.scalars(
                select(ProcessingRecoveryItem).where(
                    ProcessingRecoveryItem.run_id == uuid.UUID(run["id"])
                )
            ).all()
            == []
        )
    assert (
        env.client.get(
            f"/processing/recovery-runs/{run['id']}", headers=env.headers
        ).status_code
        == 404
    )
    # Expired idempotency keys do not make an old optimistic revision valid again.
    assert (
        env.client.post(
            "/processing/recovery-runs", json=payload, headers=env.headers
        ).status_code
        == 409
    )


def test_current_write_only_credential_can_cancel_but_cannot_read_sources(
    processing_env,
):
    env = processing_env
    run, _ = _accept(env)
    with Session(env.engine) as db:
        db.get(ApiToken, env.credential_id).scopes = ["write:operations"]
        db.commit()
    assert (
        env.client.get(
            f"/processing/recovery-runs/{run['id']}", headers=env.headers
        ).status_code
        == 403
    )
    response = env.client.post(
        f"/processing/recovery-runs/{run['id']}/cancel",
        json={"expected_version": run["version"]},
        headers=env.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_limited"] and response.json()["items"] == []
    assert response.json()["total_count"] == 0


def test_invalid_second_selection_rolls_back_all_claims(processing_env):
    env = processing_env
    selected = _selection(env)
    response = env.client.post(
        "/processing/recovery-runs",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [
                {key: selected[key] for key in ("item_id", "stage", "revision")},
                {
                    "item_id": str(uuid.uuid4()),
                    "stage": "classification",
                    "revision": "outdated",
                },
            ],
        },
        headers=env.headers,
    )
    assert response.status_code == 409
    with Session(env.engine) as db:
        assert (
            db.scalars(
                select(ProcessingWork).where(ProcessingWork.item_id == env.item_id)
            ).all()
            == []
        )
        assert (
            db.scalars(
                select(ProcessingRecoveryRun).where(
                    ProcessingRecoveryRun.principal_id == env.owner_id
                )
            ).all()
            == []
        )


def test_deleted_source_settles_waiting_run_without_an_infinite_queue(processing_env):
    env = processing_env
    run, _ = _accept(env)
    with Session(env.engine) as db:
        db.execute(delete(Item).where(Item.id == env.item_id))
        db.commit()
    with Session(env.engine) as db:
        assert maintain_processing_work(db) == 1
        db.commit()
        stored = db.get(ProcessingRecoveryRun, uuid.UUID(run["id"]))
        assert stored.status == "failed"
        entry = db.scalar(
            select(ProcessingRecoveryItem).where(
                ProcessingRecoveryItem.run_id == stored.id
            )
        )
        assert entry.reason == "item_deleted"


def test_round_robin_admits_a_new_feed_before_more_old_feed_backlog(
    processing_env, monkeypatch
):
    from app.core.config import get_settings
    from app.models.feed import Feed
    from app.models.processing_work import ProcessingDispatchState
    from app.services.feed_pipeline import upsert_item_from_parsed
    from types import SimpleNamespace

    env = processing_env
    monkeypatch.setattr(get_settings(), "processing_dispatch_batch_size", 1)
    monkeypatch.setattr(get_settings(), "processing_dispatch_per_feed", 1)
    monkeypatch.setattr(get_settings(), "processing_dispatch_max_in_flight", 1)
    _accept(env)
    identity, token = _publication(env)
    worker.execute_processing_work(identity, token)
    with Session(env.engine) as db:
        cursor = db.get(ProcessingDispatchState, 1)
        assert cursor.last_feed_id == env.feed_id
        old_feed = db.get(Feed, env.feed_id)
        newcomer = Feed(
            name="New repair feed", url=f"https://example.invalid/new-{uuid.uuid4()}"
        )
        db.add(newcomer)
        db.flush()
        for feed in (old_feed, newcomer):
            for _ in range(2):
                key = uuid.uuid4().hex
                upsert_item_from_parsed(
                    db,
                    feed,
                    SimpleNamespace(
                        url=f"https://example.invalid/{key}",
                        guid=key,
                        title="Synthetic new backlog",
                        summary="Evidence",
                        published_at=None,
                    ),
                )
        newcomer_id = newcomer.id
        db.commit()
    try:
        with Session(env.engine) as db:
            assert discover_processing_work(db, stage="classification") == 1
            selected = db.scalar(
                select(ProcessingWork).where(ProcessingWork.status == "queued")
            )
            assert selected.feed_id == newcomer_id
            db.commit()
    finally:
        with Session(env.engine) as db:
            db.execute(delete(Feed).where(Feed.id == newcomer_id))
            db.commit()


@pytest.mark.parametrize("stage", ["article", "ioc", "tagging"])
def test_each_selected_stage_commits_real_domain_results(
    processing_env, monkeypatch, stage
):
    from app.tasks.article_fetch_tasks import ArticleFetchResult

    env = processing_env
    if stage == "tagging":
        with Session(env.engine) as db:
            item = db.get(Item, env.item_id)
            item.tagging_pending = True
            item.tagging_pending_since_at = datetime.now(timezone.utc)
            item.tagging_retry_at = datetime.now(timezone.utc)
            db.commit()
    if stage == "article":
        monkeypatch.setattr(
            "app.tasks.article_fetch_tasks._fetch_candidates",
            lambda *_a, **_kw: ArticleFetchResult(
                "https://example.invalid/synthetic",
                200,
                "text/html",
                body=b"<html>synthetic</html>",
            ),
        )
        monkeypatch.setattr(
            "app.services.extraction.extract_readable_text",
            lambda _html: {
                "text": "Refreshed recovery evidence",
                "title": "Synthetic",
                "method": "test",
                "word_count": 3,
            },
        )
    run, _ = _accept(env, stage)
    with Session(env.engine) as db:
        discover_processing_work(db, stage=stage)
        db.commit()
    with Session(env.engine) as db:
        publications = prepare_processing_publications(db, canary_at=None, stage=stage)
        db.commit()
    assert len(publications) == 1
    assert worker.execute_processing_work(*publications[0])["status"] == "succeeded"
    assert _status(env, run["id"])["completed_count"] == 1
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        if stage == "article":
            assert item.status == "content_fetched"
            assert (
                item.classification_completed_version
                < item.classification_required_version
            )
        elif stage == "ioc":
            assert item.ioc_extraction_state in {"completed", "completed_empty"}
        else:
            assert not item.tagging_pending


def test_fatal_worker_exit_leaves_only_recoverable_claim_without_partial_commit(
    processing_env, monkeypatch
):
    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    original = worker._settle_attempt
    monkeypatch.setattr(
        worker,
        "_settle_attempt",
        lambda *_args: (_ for _ in ()).throw(SystemExit("synthetic worker exit")),
    )
    with pytest.raises(SystemExit):
        worker.execute_processing_work(identity, token)
    with Session(env.engine) as db:
        assert db.get(ItemClassification, env.item_id) is None
        work = db.get(ProcessingWork, identity)
        assert work.status == "running"
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    with Session(env.engine) as db:
        assert maintain_processing_work(db) == 1
        db.get(ProcessingWork, identity).next_retry_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(worker, "_settle_attempt", original)
    replacement = _publication(env)
    assert replacement[1] != token
    assert worker.execute_processing_work(*replacement)["status"] == "succeeded"


def test_failure_finalizer_reloads_cancelled_run_instead_of_restoring_retry(
    processing_env,
):
    from app.services.processing_dispatch import fail_work

    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    assert worker.claim_processing_work(identity, token)
    current = _status(env, run["id"])
    with Session(env.engine) as failure_db:
        work = failure_db.scalar(
            select(ProcessingWork)
            .where(ProcessingWork.id == identity)
            .with_for_update()
        )
        stale = failure_db.get(ProcessingRecoveryRun, uuid.UUID(run["id"]))
        assert stale.status == "running"
        response = env.client.post(
            f"/processing/recovery-runs/{run['id']}/cancel",
            json={"expected_version": current["version"]},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text
        fail_work(failure_db, work, reason="worker_failed")
        failure_db.commit()
        assert work.status == "cancelled" and work.next_retry_at is None
        assert failure_db.get(ProcessingRecoveryRun, stale.id).status == "cancelled"


@pytest.mark.parametrize("stop_reason", ["cancelled", "authorization_changed"])
def test_stopped_run_does_not_suppress_independent_automatic_obligation(
    processing_env, stop_reason
):
    from app.services.processing_dispatch import fail_work

    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    assert worker.claim_processing_work(identity, token)
    if stop_reason == "cancelled":
        current = _status(env, run["id"])
        response = env.client.post(
            f"/processing/recovery-runs/{run['id']}/cancel",
            json={"expected_version": current["version"]},
            headers=env.headers,
        )
        assert response.status_code == 200
    else:
        with Session(env.engine) as db:
            work = db.scalar(
                select(ProcessingWork)
                .where(ProcessingWork.id == identity)
                .with_for_update()
            )
            fail_work(db, work, reason="authorization_changed", retryable=False)
            db.commit()
    with Session(env.engine) as db:
        assert discover_processing_work(db, stage="classification") == 1
        work = db.get(ProcessingWork, identity)
        assert work.recovery_run_id is None and work.attempts == 1
        db.commit()
    replacement = _publication(env)
    assert worker.execute_processing_work(*replacement)["status"] == "succeeded"
    status = _status(env, run["id"])
    assert status["status"] == ("cancelled" if stop_reason == "cancelled" else "failed")
    assert status["completed_count"] == 0
    with Session(env.engine) as db:
        assert db.get(ProcessingWork, identity).attempts == 2


def test_stopped_run_cannot_reset_automatic_attempt_allowance(processing_env):
    env = processing_env
    run, _ = _accept(env)
    with Session(env.engine) as db:
        work = db.scalar(
            select(ProcessingWork).where(ProcessingWork.item_id == env.item_id)
        )
        work.attempts = 5
        db.commit()
    response = env.client.post(
        f"/processing/recovery-runs/{run['id']}/cancel",
        json={"expected_version": run["version"]},
        headers=env.headers,
    )
    assert response.status_code == 200
    with Session(env.engine) as db:
        for _ in range(3):
            assert discover_processing_work(db, stage="classification") == 0
        assert (
            db.scalar(
                select(ProcessingWork.attempts).where(
                    ProcessingWork.item_id == env.item_id
                )
            )
            == 5
        )


def test_expired_published_token_cannot_claim_without_maintenance(processing_env):
    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert not worker.claim_processing_work(identity, token)
    with Session(env.engine) as db:
        assert db.get(ProcessingWork, identity).attempts == 0


def test_expired_worker_cannot_commit_domain_results_without_maintenance(
    processing_env, monkeypatch
):
    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    original = worker._settle_attempt

    def expire_before_settlement(db, work):
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        original(db, work)

    monkeypatch.setattr(worker, "_settle_attempt", expire_before_settlement)
    assert worker.execute_processing_work(identity, token)["status"] == "retry_wait"
    with Session(env.engine) as db:
        assert db.get(ItemClassification, env.item_id) is None
        assert db.get(ProcessingWork, identity).reason == "worker_interrupted"


def test_terminal_cleanup_and_selected_retry_share_one_admission_boundary(
    processing_env,
):
    from app.services.processing_dispatch import (
        admission_lock,
        fail_work,
        prune_recovery_history,
    )

    env = processing_env
    run, _ = _accept(env)
    identity, token = _publication(env)
    assert worker.claim_processing_work(identity, token)
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        fail_work(db, work, reason="worker_failed", retryable=False)
        db.get(ProcessingRecoveryRun, uuid.UUID(run["id"])).updated_at = datetime.now(
            timezone.utc
        ) - timedelta(days=8)
        db.commit()
    selected = _selection(env)
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "items": [{key: selected[key] for key in ("item_id", "stage", "revision")}],
    }
    started = Event()

    def accept():
        started.set()
        return env.client.post(
            "/processing/recovery-runs", json=payload, headers=env.headers
        )

    with Session(env.engine) as cleanup_db, ThreadPoolExecutor(max_workers=1) as pool:
        admission_lock(cleanup_db)
        future = pool.submit(accept)
        assert started.wait(5)
        assert prune_recovery_history(cleanup_db) == 1
        cleanup_db.commit()
        response = future.result(timeout=10)
        assert response.status_code == 202, response.text
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        assert (
            work.status == "waiting"
            and str(work.recovery_run_id) == response.json()["id"]
        )


def test_maintenance_wait_is_bounded_by_shared_admission_fence(processing_env):
    from sqlalchemy.exc import OperationalError
    from app.db.budgets import database_operation
    from app.services.processing_dispatch import admission_lock

    env = processing_env
    _accept(env)
    identity, token = _publication(env)
    assert worker.claim_processing_work(identity, token)
    with Session(env.engine) as db:
        db.get(ProcessingWork, identity).lease_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=1)
        db.commit()

    def attempt_maintenance():
        with (
            Session(env.engine) as db,
            database_operation(db, operation="repair", timeout_seconds=0.1),
        ):
            maintain_processing_work(db)
            db.commit()

    with Session(env.engine) as admission_db, ThreadPoolExecutor(max_workers=1) as pool:
        admission_lock(admission_db)
        future = pool.submit(attempt_maintenance)
        with pytest.raises(OperationalError):
            future.result(timeout=5)
        admission_db.rollback()
    with Session(env.engine) as db:
        assert db.get(ProcessingWork, identity).status == "running"
        assert maintain_processing_work(db) == 1
        db.commit()
        assert db.get(ProcessingWork, identity).status == "retry_wait"
