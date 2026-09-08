from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis
from celery.contrib.testing.worker import start_worker
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    before_task_publish,
    after_task_publish,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import session as session_module
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.article import Article
from app.models.data_policy import DataPolicyState, UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.user import User
from app.schemas.ai import AISettingsUpdate
from app.schemas.data_policy import (
    HandlingLabelCreateRequest,
    HandlingLabelUpdateRequest,
)
from app.schemas.exports import ArticleExportFilters, ArticleExportOptions
from app.services.ai_config import apply_ai_settings_update, get_or_create_ai_settings
from app.services.ai_ops import queue_ai_task_run, start_ai_task_run, finish_ai_task_run
from app.services.ai_integration import test_ai_connection as run_connection_test
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import (
    DataPolicyRevisionConflict,
    create_handling_label,
    current_data_policy_revision,
    data_access_context_for_authorization,
    fence_data_access_context,
    update_data_policy_mode,
    update_handling_label,
)
from app.services.export_artifacts import (
    generate_export_artifact,
    remove_export_artifact,
)
from app.services.export_lock import acquire_export_lock
from app.services.export_query import (
    ExportAuthorizationChangedError,
    ExportTextProjection,
    build_export_query_context,
    iter_export_records,
    load_export_item_ids,
)
from app.services.feed_pipeline import upsert_item_from_parsed
from app.tasks import feed_task_coordination, feed_tasks
from app.tasks.celery_app import celery_app
from scripts.capacity_results import seal_result
from tests.capacity.deadline_probes import observe_deadlines
from tests.capacity.sustained import paced_lane
from tests.capacity.workload_support import (
    Measurements,
    PROFILES,
    QUEUES,
    local_sources,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("THREATLENS_CAPACITY_PROFILE"),
    reason="explicit capacity harness only",
)
ROOT = Path(__file__).resolve().parents[3]


def _seed(engine, profile, base):
    with Session(engine) as db:
        owner = User(
            email=f"capacity-{uuid.uuid4()}@example.invalid",
            password_hash="unused-synthetic-account",
            role="admin",
            is_active=True,
            is_approved=True,
        )
        db.add(owner)
        db.flush()
        seed_feed = Feed(
            name="Capacity retained catalog",
            url=f"{base}/seed",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        feeds = [
            Feed(
                name=f"Capacity feed {i}",
                url=f"{base}/feed/{i}",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            )
            for i in range(profile["feeds"])
        ]
        db.add_all([seed_feed, *feeds])
        db.flush()
        body = (
            "Retained vulnerability evidence. " * (profile["article_bytes"] // 32 + 1)
        )[: profile["article_bytes"]]
        for index in range(profile["seed_items"]):
            item, _, _ = upsert_item_from_parsed(
                db,
                seed_feed,
                SimpleNamespace(
                    url=f"{base}/retained/{index}",
                    guid=f"retained-{index}",
                    title=f"Retained advisory {index}",
                    summary="Security evidence for capacity validation",
                    published_at=None,
                ),
            )
            item.ioc_extraction_state = "completed_empty"
            db.add(
                Article(item_id=item.id, final_url=item.url, http_status=200, text=body)
            )
        label = create_handling_label(
            db,
            actor_user_id=owner.id,
            payload=HandlingLabelCreateRequest(
                expected_policy_revision=current_data_policy_revision(db),
                key=f"capacity-{uuid.uuid4().hex[:12]}",
                name="Capacity review",
            ),
        ).label
        ai = get_or_create_ai_settings(db)
        apply_ai_settings_update(
            ai,
            AISettingsUpdate(
                base_url=base,
                model="capacity-local",
                auto_enrich_new_items=False,
                request_timeout_seconds=5,
                request_max_retries=0,
            ),
        )
        db.commit()
        update_data_policy_mode(
            db,
            mode="enforced",
            expected_revision=current_data_policy_revision(db),
            actor_user_id=owner.id,
        )
        db.commit()
        return owner.id, label.id, seed_feed.id, [feed.id for feed in feeds]


def _export(engine, owner_id, seed_feed_id, settings, metrics, iterations):
    for index in range(iterations):
        with metrics.operation("export") as operation:
            artifact = None
            with Session(engine) as db:
                try:
                    owner = db.get(User, owner_id)
                    authorization = authorization_context_for_user(db, owner)
                    access = data_access_context_for_authorization(db, authorization)
                    filters = ArticleExportFilters(feed_ids=[seed_feed_id])
                    options = ArticleExportOptions(
                        include_article_text=index % 2 == 0, include_iocs=False
                    )
                    context = build_export_query_context(
                        user_id=owner_id, filters=filters, data_access=access
                    )
                    with acquire_export_lock(user_id=owner_id, settings=settings):
                        ids = load_export_item_ids(
                            db, context=context, limit=settings.export_max_items
                        )
                        records = iter_export_records(
                            db,
                            item_ids=ids,
                            context=context,
                            include_iocs=False,
                            text_projection=ExportTextProjection(
                                include_article_text=options.include_article_text
                            ),
                            max_payload_bytes=settings.export_max_uncompressed_bytes,
                        )
                        artifact = generate_export_artifact(
                            records,
                            item_count=len(ids),
                            export_format="jsonl",
                            filters=filters,
                            options=options,
                            max_uncompressed_bytes=settings.export_max_uncompressed_bytes,
                        )
                        fence_data_access_context(db, access)
                        assert artifact.file_size > 0
                        db.commit()
                    metrics.outcome("exports_succeeded")
                    operation["outcome"] = "succeeded"
                except (ExportAuthorizationChangedError, DataPolicyRevisionConflict):
                    db.rollback()
                    metrics.outcome("exports_policy_conflict")
                    operation["outcome"] = "policy_conflict"
                finally:
                    if artifact is not None:
                        remove_export_artifact(artifact.path)
        time.sleep(0.015)


def _governance(engine, owner_id, label_id, metrics, iterations, offset=0):
    from app.models.data_policy import HandlingLabel

    for index in range(offset, offset + iterations):
        with metrics.operation("governance"):
            with Session(engine) as db:
                label = db.get(HandlingLabel, label_id)
                update_handling_label(
                    db,
                    label_id=label_id,
                    actor_user_id=owner_id,
                    payload=HandlingLabelUpdateRequest(
                        expected_revision=label.revision,
                        description=f"Capacity revision {index}",
                    ),
                )
                db.commit()
            metrics.outcome("governance_succeeded")
        time.sleep(0.02)


def _ai(engine, metrics, iterations):
    for _ in range(iterations):
        with metrics.operation("ai_connection") as operation:
            with Session(engine) as db:
                run = queue_ai_task_run(
                    db, task_type="connection_test", trigger_source="manual"
                )
                db.commit()
                start_ai_task_run(db, run_id=run.id, worker_name="capacity-service")
                db.commit()
                result = run_connection_test(db, task_run_id=run.id)
                finish_ai_task_run(
                    db, run_id=run.id, status="ready" if result.success else "error"
                )
                db.commit()
                if result.success:
                    metrics.outcome("ai_succeeded")
                    operation["outcome"] = "succeeded"
                else:
                    # Governance can invalidate the pre-I/O reservation. Count
                    # this explicit safe pause; never retry an ambiguous send.
                    assert result.error.startswith(
                        "AI provider request is paused because its authorization changed repeatedly"
                    ), result.error
                    receipts = db.scalars(
                        select(AIProviderAttemptReceipt).where(
                            AIProviderAttemptReceipt.task_run_id_snapshot == run.id
                        )
                    ).all()
                    assert receipts and all(
                        receipt.state == "voided" and receipt.io_outcome == "not_sent"
                        for receipt in receipts
                    )
                    metrics.outcome("ai_policy_conflict")
                    operation["outcome"] = "policy_conflict"


def _wait_for_pipeline(
    engine, broker, feed_ids, expected, metrics, started, timeout, jobs
):
    last_repair = started - 5
    while time.monotonic() - started < timeout:
        for job in jobs:
            if job.done():
                job.result()
        with Session(engine) as db:
            ready = db.scalar(
                select(func.count())
                .select_from(Item)
                .join(Article, Article.item_id == Item.id)
                .join(ItemClassification, ItemClassification.item_id == Item.id)
                .where(
                    Item.feed_id.in_(feed_ids),
                    Article.text.is_not(None),
                    Item.classification_completed_version
                    == Item.classification_required_version,
                    Item.ioc_extraction_state.in_(("completed", "completed_empty")),
                )
            )
        depth = sum(broker.llen(queue) for queue in QUEUES) + broker.hlen("unacked")
        expected_now = expected() if callable(expected) else expected
        if ready == expected_now and depth == 0:
            metrics.outcome("ingestion_recovered")
            return round((time.monotonic() - started) * 1000, 3)
        if depth == 0 and time.monotonic() - last_repair >= 5:
            feed_tasks.dispatch_items_missing_iocs.delay()
            metrics.outcome("ioc_repair_dispatches")
            last_repair = time.monotonic()
        time.sleep(0.05)
    raise AssertionError(
        f"pipeline did not recover: {ready}/{expected} classified articles, queue depth={depth}"
    )


def test_concurrent_workload(database_engine, test_redis_url, monkeypatch):
    assert not os.environ.get("THREATLENS_TEST_DATABASE_URL"), (
        "capacity requires fixture-owned PostgreSQL"
    )
    assert not os.environ.get("THREATLENS_TEST_REDIS_URL"), (
        "capacity requires fixture-owned Redis"
    )
    assert test_redis_url, (
        "capacity requires disposable Docker Redis; an in-memory fallback is insufficient"
    )
    profile_name = os.environ["THREATLENS_CAPACITY_PROFILE"]
    profile = dict(PROFILES[profile_name])
    profile["ioc_repair_interval_seconds"] = 5
    if profile_name == "sustained":
        profile["duration_seconds"] = int(os.environ["THREATLENS_CAPACITY_DURATION"])
    budgets = json.loads((ROOT / "docs/reviews/capacity/budgets.json").read_text())[
        profile_name
    ]
    for name, value in {
        "AI_ENABLED": "true",
        "ALLOW_PRIVATE_NETWORK_FETCH": "true",
        "ALLOW_PRIVATE_NETWORK_AI": "true",
        "REDIS_URL": test_redis_url,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    settings = get_settings()
    # Instrument the same connection budget as the application (default5 + overflow10).
    options = session_module._engine_options(
        database_engine.url.render_as_string(hide_password=False)
    )
    options["connect_args"]["application_name"] = f"threatlens-capacity-{profile_name}"
    engine = create_engine(database_engine.url, **options)
    monkeypatch.setattr(
        session_module,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, class_=Session),
    )
    broker = redis.Redis.from_url(test_redis_url, decode_responses=True)
    monkeypatch.setattr(feed_tasks, "settings", settings)
    monkeypatch.setattr(feed_task_coordination, "settings", settings)
    monkeypatch.setattr(feed_tasks, "redis_client", broker)
    monkeypatch.setattr(feed_task_coordination, "redis_client", broker)
    monkeypatch.setitem(celery_app.conf, "broker_url", test_redis_url)
    monkeypatch.setitem(celery_app.conf, "result_backend", test_redis_url)
    monkeypatch.setitem(celery_app.conf, "task_always_eager", False)
    monkeypatch.setitem(celery_app.conf, "result_backend_thread_safe", True)
    metrics = Measurements()
    task_starts = {}
    task_errors = []

    def before_task(task_id=None, task=None, **_kwargs):
        metrics.task_started(task_id, task)
        task_starts[task_id] = time.perf_counter()

    def after_task(task_id=None, task=None, retval=None, **_kwargs):
        if isinstance(retval, dict) and retval.get("status") == "error":
            task_errors.append({"task": task.name, "error_type": "returned_error"})
        started = task_starts.pop(task_id, None)
        if started is not None:
            with metrics.lock:
                metrics.latencies.setdefault(
                    f"task:{task.name.rsplit('.', 1)[-1]}", []
                ).append((time.perf_counter() - started) * 1000)

    def failed_task(sender=None, exception=None, **_kwargs):
        task_errors.append(
            {"task": sender.name, "error_type": type(exception).__name__}
        )

    def before_publish(headers=None, **_kwargs):
        headers["capacity_published_ns"] = time.monotonic_ns()

    def after_publish(headers=None, **_kwargs):
        metrics.published(headers)

    before_task_publish.connect(before_publish, weak=False)
    after_task_publish.connect(after_publish, weak=False)
    task_prerun.connect(before_task, weak=False)
    task_postrun.connect(after_task, weak=False)
    task_failure.connect(failed_task, weak=False)
    stop = threading.Event()
    sampler = threading.Thread(
        target=metrics.sample, args=(engine, broker, stop), daemon=True
    )
    began = time.monotonic()
    try:
        source_state = {}
        with local_sources(profile, source_state) as base:
            sampler.start()
            observe_deadlines(metrics, base)
            owner_id, label_id, seed_feed_id, feed_ids = _seed(engine, profile, base)
            # Consumer outage: publish real application tasks before any worker exists.
            for feed_id in feed_ids:
                feed_tasks.fetch_feed.apply_async(
                    args=[str(feed_id)], kwargs={"force": True}
                )
            outage_depth = broker.llen("ingest")
            assert outage_depth == len(feed_ids)
            time.sleep(0.15)
            recovery_started = time.monotonic()
            with start_worker(
                celery_app,
                pool="threads",
                concurrency=profile["worker_concurrency"],
                queues=list(QUEUES),
                perform_ping_check=False,
                shutdown_timeout=20,
                without_heartbeat=False,
                heartbeat_interval=0.2,
            ):
                duration = profile.get("duration_seconds")
                with ThreadPoolExecutor(max_workers=5) as executor:
                    if duration:
                        jobs = [
                            executor.submit(
                                paced_lane,
                                lambda _: _export(
                                    engine, owner_id, seed_feed_id, settings, metrics, 1
                                ),
                                duration_seconds=duration,
                                interval_seconds=profile["service_interval_seconds"],
                            ),
                            executor.submit(
                                paced_lane,
                                lambda index: _governance(
                                    engine, owner_id, label_id, metrics, 1, offset=index
                                ),
                                duration_seconds=duration,
                                interval_seconds=profile["service_interval_seconds"],
                            ),
                            executor.submit(
                                paced_lane,
                                lambda _: _ai(engine, metrics, 1),
                                duration_seconds=duration,
                                interval_seconds=profile["service_interval_seconds"],
                            ),
                        ]

                        def publish_batch(index):
                            if index:
                                for feed_id in feed_ids:
                                    feed_tasks.fetch_feed.apply_async(
                                        args=[str(feed_id)], kwargs={"force": True}
                                    )

                        feed_job = executor.submit(
                            paced_lane,
                            publish_batch,
                            duration_seconds=duration,
                            interval_seconds=profile["feed_interval_seconds"],
                        )

                        def repair_iocs(_index):
                            feed_tasks.dispatch_items_missing_iocs.delay()
                            metrics.outcome("ioc_repair_dispatches")

                        repair_job = executor.submit(
                            paced_lane,
                            repair_iocs,
                            duration_seconds=duration,
                            interval_seconds=5,
                        )
                        completed = [job.result(timeout=duration + 120) for job in jobs]
                        feed_batches = feed_job.result(timeout=120)
                        repair_job.result(timeout=120)

                        def expected():
                            return source_state.get("issued_items", 0)

                        drained_started = time.monotonic()
                        recovery_ms = _wait_for_pipeline(
                            engine,
                            broker,
                            feed_ids,
                            expected,
                            metrics,
                            drained_started,
                            budgets["queue_recovery_ms"] / 1000,
                            jobs,
                        )
                        expected = expected()
                    else:
                        jobs = [
                            executor.submit(
                                _export,
                                engine,
                                owner_id,
                                seed_feed_id,
                                settings,
                                metrics,
                                profile["operations"],
                            ),
                            executor.submit(
                                _governance,
                                engine,
                                owner_id,
                                label_id,
                                metrics,
                                profile["operations"],
                            ),
                            executor.submit(
                                _ai, engine, metrics, profile["operations"]
                            ),
                        ]
                        expected = profile["items_per_feed"] * (profile["feeds"] + 2)
                        recovery_ms = _wait_for_pipeline(
                            engine,
                            broker,
                            feed_ids,
                            expected,
                            metrics,
                            recovery_started,
                            budgets["queue_recovery_ms"] / 1000,
                            jobs,
                        )
                        for job in jobs:
                            job.result(timeout=120)
                        completed = [profile["operations"]] * 3
                        feed_batches = 1
                # Confirm at least one export once the policy revision settles.
                _export(engine, owner_id, seed_feed_id, settings, metrics, 1)
                _ai(engine, metrics, 1)
                observe_deadlines(metrics, base)
            with Session(engine) as db:
                usage_rows = dict(
                    db.execute(
                        select(AIUsageEvent.success, func.count()).group_by(
                            AIUsageEvent.success
                        )
                    ).all()
                )
                receipt_rows = dict(
                    db.execute(
                        select(AIProviderAttemptReceipt.state, func.count()).group_by(
                            AIProviderAttemptReceipt.state
                        )
                    ).all()
                )
                run_rows = dict(
                    db.execute(
                        select(AITaskRun.status, func.count())
                        .where(AITaskRun.task_type == "connection_test")
                        .group_by(AITaskRun.status)
                    ).all()
                )
                postgres_version = (
                    db.connection().exec_driver_sql("SHOW server_version").scalar_one()
                )
                mode = db.get(DataPolicyState, 1).mode
            stop.set()
            sampler.join(timeout=3)
            result = metrics.report()
            result.update(
                {
                    "schema_version": 1,
                    "run_id": os.environ.get("THREATLENS_CAPACITY_RUN_ID"),
                    "profile": profile_name,
                    "workload": profile,
                    "git_revision": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "cpu_count": os.cpu_count(),
                    "elapsed_seconds": round(time.monotonic() - began, 3),
                    "policy_mode": mode,
                    "ai_usage_rows_by_success": usage_rows,
                    "ai_receipts_by_state": receipt_rows,
                    "ai_connection_runs_by_status": run_rows,
                    "task_errors": task_errors,
                    "environment": {
                        "postgres_version": postgres_version,
                        "redis_version": broker.info("server")["redis_version"],
                        "celery_pool": "threads",
                        "celery_heartbeat_seconds": 0.2,
                        "database_pool_size": engine.pool.size(),
                        "database_max_overflow": engine.pool._max_overflow,
                    },
                }
            )
            result["queue"].update(
                {
                    "outage_backlog": outage_depth,
                    "recovery_ms": recovery_ms,
                    "expected_ingested_articles": expected,
                    "feed_batches": feed_batches,
                    "recovery_scope": "post_load_drain"
                    if duration
                    else "consumer_outage_and_startup",
                }
            )
            observed = {
                name + "_p95_ms": result["latencies"][
                    name + (":succeeded" if name != "governance" else "")
                ]["p95_ms"]
                for name in ("export", "governance", "ai_connection")
            }
            observed.update(
                {
                    "queue_recovery_ms": recovery_ms,
                    "process_rss_increase_bytes": result["memory"][
                        "process_rss_increase_bytes"
                    ],
                    "sampled_lock_waiting_query_age_peak_ms": result["database"][
                        "sampled_lock_waiting_query_age_peak_ms"
                    ],
                }
            )
            violations = {
                key: {"observed": observed[key], "budget": budget}
                for key, budget in budgets.items()
                if observed[key] > budget
            }
            result["budgets"] = budgets
            result["budget_violations"] = violations
            result["workload_completed"] = {
                "exports": completed[0],
                "governance": completed[1],
                "ai": completed[2],
            }
            result["status"] = "passed"
            seal_result(
                result,
                target_id=os.environ.get("THREATLENS_CAPACITY_TARGET_ID", "unlabeled"),
                limits=json.loads(os.environ.get("THREATLENS_CAPACITY_LIMITS", "{}")),
            )
            output = Path(os.environ["THREATLENS_CAPACITY_OUTPUT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"Capacity measurements: {output}")
            assert not task_errors
            assert not metrics.sampler_errors
            assert metrics.outcomes.get("exports_succeeded", 0) >= 1
            assert metrics.outcomes.get("ai_succeeded", 0) >= 1
            assert (
                metrics.outcomes.get("ai_succeeded", 0)
                + metrics.outcomes.get("ai_policy_conflict", 0)
                == completed[2] + 1
            )
            assert metrics.outcomes.get("governance_succeeded") == completed[1]
            assert usage_rows.get(True) == metrics.outcomes["ai_succeeded"]
            assert receipt_rows.get("succeeded") == metrics.outcomes["ai_succeeded"]
            assert set(receipt_rows) <= {"succeeded", "voided"}
            assert run_rows.get("ready") == metrics.outcomes["ai_succeeded"]
            assert run_rows.get("error", 0) == metrics.outcomes.get(
                "ai_policy_conflict", 0
            )
            assert set(run_rows) <= {"ready", "error"}
            assert not violations, violations
    finally:
        stop.set()
        if sampler.is_alive():
            sampler.join(timeout=3)
        before_task_publish.disconnect(before_publish)
        after_task_publish.disconnect(after_publish)
        task_prerun.disconnect(before_task)
        task_postrun.disconnect(after_task)
        task_failure.disconnect(failed_task)
        consumer = celery_app.backend.result_consumer
        consumer.stop()
        # Celery stop() closes but retains the PubSub object. Late AsyncResult
        # finalizers would otherwise reconnect after the fixture removes Redis.
        consumer._pubsub = None
        celery_app.close()
        broker.close()
        engine.dispose()
