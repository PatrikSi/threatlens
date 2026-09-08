from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import redis
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import session as session_module
from app.models.article import Article
from app.models.item import Item
from app.tasks import feed_task_coordination, feed_tasks
from app.tasks.celery_app import celery_app
from scripts.capacity_results import seal_result
from tests.capacity.owned_worker import OwnedWorker
from tests.capacity.test_concurrent_workload import ROOT, _seed, _wait_for_pipeline
from tests.capacity.workload_support import Measurements, local_sources, summarize

pytestmark = pytest.mark.skipif(
    os.environ.get("THREATLENS_CAPACITY_PROFILE") != "recovery",
    reason="explicit recovery harness only",
)


def test_real_worker_death_and_durable_broker_recovery(
    database_engine, capacity_redis_service, monkeypatch, tmp_path
):
    service = capacity_redis_service
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for key, value in {
        "REDIS_URL": service.url,
        "AI_ENABLED": "true",
        "ALLOW_PRIVATE_NETWORK_FETCH": "true",
        "ALLOW_PRIVATE_NETWORK_AI": "true",
        "DISPATCH_ITEMS_MISSING_ARTICLES_AFTER_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = get_settings()
    options = session_module._engine_options(str(database_engine.url))
    options["connect_args"]["application_name"] = "threatlens-capacity-recovery"
    engine = create_engine(database_engine.url, **options)
    monkeypatch.setattr(
        session_module,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, class_=Session),
    )
    broker = redis.Redis.from_url(
        service.url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1
    )
    for module in (feed_tasks, feed_task_coordination):
        monkeypatch.setattr(module, "settings", settings)
        monkeypatch.setattr(module, "redis_client", broker)
    for key in ("broker_url", "result_backend"):
        monkeypatch.setitem(celery_app.conf, key, service.url)
    monkeypatch.setitem(celery_app.conf, "result_backend_thread_safe", True)
    profile = {
        "seed_items": 0,
        "article_bytes": 8192,
        "feeds": 1,
        "items_per_feed": 1,
        "provider_delay_seconds": 0.1,
        "worker_concurrency": 1,
        "worker_pool": "prefork",
        "redis_persistence": "aof_appendfsync_always",
        "faults": [
            "redis_sigkill_before_consumer",
            "prefork_task_child_sigkill_during_article",
        ],
        "article_repair_eligibility_seconds": 0,
        "ioc_repair_interval_seconds": 5,
    }
    state = {"article_started": threading.Event(), "article_release": threading.Event()}
    metrics = Measurements()
    worker = None
    stop = threading.Event()
    sampler = threading.Thread(
        target=metrics.sample, args=(engine, broker, stop), daemon=True
    )
    began = time.monotonic()
    try:
        with local_sources(profile, state) as base:
            _, _, _, feed_ids = _seed(engine, profile, base)
            feed_tasks.fetch_feed.apply_async(
                args=[str(feed_ids[0])],
                kwargs={"force": True},
                headers={"capacity_published_ns": time.monotonic_ns()},
            )
            accepted = broker.lrange("ingest", 0, -1)
            assert len(accepted) == 1
            assert json.loads(accepted[0])["headers"]["ignore_result"] is True
            assert celery_app.backend.result_consumer._pubsub is None
            broker_down = time.monotonic()
            service.crash()
            with pytest.raises(redis.ConnectionError):
                broker.ping()
            service.restart()
            assert broker.lrange("ingest", 0, -1) == accepted
            broker_restart_ms = (time.monotonic() - broker_down) * 1000
            sampler.start()
            worker = OwnedWorker(
                directory=tmp_path,
                database_url=database_engine.url.render_as_string(hide_password=False),
                redis_url=service.url,
            )
            worker.wait_ready()
            assert state["article_started"].wait(timeout=30), (
                "worker did not enter gated article request"
            )
            task = next(
                event
                for event in reversed(worker.events())
                if event.get("task_name", "").endswith(".fetch_article")
                and event["kind"] == "task_started"
            )
            item_id = task["args"][0]
            with Session(engine) as db:
                with pytest.raises(OperationalError):
                    db.execute(
                        select(Item)
                        .where(Item.id == item_id)
                        .with_for_update(nowait=True)
                    ).scalar_one()
                db.rollback()
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(Article)
                        .where(Article.item_id == item_id)
                    )
                    == 0
                )
            interrupted = time.monotonic()
            killed_pid = worker.kill_task_child(task["task_id"])
            state["article_release"].set()
            # Exercise durable repair in addition to Celery's late-ack redelivery.
            feed_tasks.dispatch_items_missing_articles.delay()
            celery_app.send_task(
                "app.tasks.feed_tasks.dispatch_items_missing_iocs", ignore_result=True
            )
            recovery_ms = _wait_for_pipeline(
                engine, broker, feed_ids, 3, metrics, interrupted, 90, []
            )
            events = worker.events()
            repeats = [
                event
                for event in events
                if event["kind"] == "task_started"
                and event.get("task_id") == task["task_id"]
            ]
            assert len(repeats) >= 2
            assert any(event["pid"] != killed_pid for event in repeats)
            errors = [
                event
                for event in events
                if event["kind"] == "task_finished"
                and (
                    event.get("state") == "FAILURE"
                    or event.get("result_status") == "error"
                )
            ]
            assert not errors
            with Session(engine) as db:
                item_count = db.scalar(
                    select(func.count())
                    .select_from(Item)
                    .where(Item.feed_id.in_(feed_ids))
                )
                article_count = db.scalar(
                    select(func.count())
                    .select_from(Article)
                    .join(Item, Item.id == Article.item_id)
                    .where(Item.feed_id.in_(feed_ids))
                )
                version = (
                    db.connection().exec_driver_sql("SHOW server_version").scalar_one()
                )
            assert item_count == article_count == 3
            assert celery_app.backend.result_consumer._pubsub is None
            assert list(broker.scan_iter("celery-task-meta-*")) == []
            waits = [
                (event["monotonic_ns"] - event["published_ns"]) / 1e6
                for event in events
                if event["kind"] == "task_started"
                and event.get("published_ns") is not None
            ]
            stop.set()
            sampler.join(timeout=3)
            assert not metrics.sampler_errors
            result = metrics.report()
            result.update(
                status="passed",
                run_id=os.environ["THREATLENS_CAPACITY_RUN_ID"],
                profile="recovery",
                workload=profile,
                git_revision=os.environ.get("THREATLENS_CAPACITY_SOURCE_REVISION")
                or subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                recorded_at=datetime.now(timezone.utc).isoformat(),
                platform=platform.platform(),
                python=platform.python_version(),
                environment={
                    "postgres_version": version,
                    "redis_version": broker.info("server")["redis_version"],
                    "celery_pool": "prefork",
                },
                budgets={"worker_recovery_ms": 90000, "broker_restart_ms": 30000},
                budget_violations={},
                task_errors=errors,
                elapsed_seconds=round(time.monotonic() - began, 3),
                faults={
                    "accepted_messages_before_crash": len(accepted),
                    "accepted_messages_survived": True,
                    "broker_restart_ms": round(broker_restart_ms, 3),
                    "worker_recovery_ms": recovery_ms,
                    "killed_process_role": "prefork_task_child",
                    "task_redeliveries": len(repeats) - 1,
                    "durable_article_repair_dispatched": True,
                    "producer_restart_required": False,
                    "unused_result_subscriptions": 0,
                    "items": item_count,
                    "articles": article_count,
                },
            )
            result["budget_violations"] = {
                key: {"observed": result["faults"][key], "budget": value}
                for key, value in result["budgets"].items()
                if result["faults"][key] > value
            }
            result["status"] = "failed" if result["budget_violations"] else "passed"
            result["latencies"]["queue_wait:all"] = summarize(waits)
            result["source_dirty"] = (
                os.environ.get("THREATLENS_CAPACITY_SOURCE_DIRTY") == "true"
            )
            seal_result(
                result,
                target_id=os.environ["THREATLENS_CAPACITY_TARGET_ID"],
                limits=json.loads(os.environ["THREATLENS_CAPACITY_LIMITS"]),
            )
            output = Path(os.environ["THREATLENS_CAPACITY_OUTPUT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            assert broker_restart_ms <= 30000
    finally:
        stop.set()
        if sampler.is_alive():
            sampler.join(timeout=3)
        state["article_release"].set()
        if worker:
            worker.close()
        consumer = celery_app.backend.result_consumer
        consumer.stop()
        consumer._pubsub = None
        celery_app.close()
        broker.close()
        engine.dispose()
