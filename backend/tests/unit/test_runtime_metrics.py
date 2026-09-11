from __future__ import annotations

import multiprocessing
import os
import socket
import threading
import time
from functools import lru_cache
from types import SimpleNamespace

import pytest
import redis

from app.core import runtime_metrics
from app.services.outbound_deadline import OutboundDeadlineExceeded, outbound_deadline


@pytest.fixture(autouse=True)
def metric_io(monkeypatch):
    pools = {
        "write": (threading.BoundedSemaphore(2), 2),
        "read": (threading.BoundedSemaphore(1), 1),
    }
    monkeypatch.setattr(runtime_metrics, "_delivery_slots", pools["write"][0])
    monkeypatch.setattr(runtime_metrics, "_collection_slots", pools["read"][0])
    monkeypatch.setattr(
        runtime_metrics, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://metric-test.invalid:6379/0"),
    )

    @lru_cache(maxsize=4)
    def client(url):
        return redis.Redis.from_url(
            url, socket_connect_timeout=0.1, socket_timeout=0.1,
            decode_responses=True, max_connections=2,
        )

    monkeypatch.setattr(runtime_metrics, "_client", client)
    yield pools
    # No worker may survive restoration of these test-local dependencies.
    for slots, capacity in pools.values():
        for _ in range(capacity):
            assert slots.acquire(timeout=1)


def _unknown(result):
    assert result == {
        f"{event}_last_15m": None for event in runtime_metrics.RUNTIME_EVENTS
    }


@pytest.mark.parametrize("kind", ["write", "read"])
def test_slow_metric_dns_cannot_hold_deadline_failure_or_health_collection(
    monkeypatch, kind,
):
    started = threading.Event()
    finish = threading.Event()
    returned = threading.Event()

    def resolve(host, *_args, **_kwargs):
        assert host == "metric-test.invalid"
        started.set()
        try:
            assert finish.wait(2)
            raise OSError("synthetic DNS failure")
        finally:
            returned.set()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    began = time.monotonic()
    try:
        if kind == "write":
            with pytest.raises(OutboundDeadlineExceeded):
                with outbound_deadline(0):
                    pytest.fail("expired operation must not start")
        else:
            _unknown(runtime_metrics.collect_runtime_events())
        assert time.monotonic() - began < 0.5
        assert started.wait(1)
        assert not returned.is_set()
    finally:
        finish.set()
        assert returned.wait(1)


@pytest.mark.parametrize("kind", ["write", "read"])
def test_metric_capacity_drops_excess_work_without_a_pending_queue(
    monkeypatch, metric_io, kind,
):
    slots, capacity = metric_io[kind]
    started = threading.Event()
    finish = threading.Event()
    calls = []

    def blocked(*args):
        calls.append(args)
        if len(calls) == capacity:
            started.set()
        assert finish.wait(2)
        return {f"{event}_last_15m": 0 for event in runtime_metrics.RUNTIME_EVENTS}

    helper = "_write_runtime_event" if kind == "write" else "_read_runtime_events"
    monkeypatch.setattr(runtime_metrics, helper, blocked)
    invoke = (
        lambda: runtime_metrics.record_runtime_event("outbound_deadline")
    ) if kind == "write" else runtime_metrics.collect_runtime_events
    try:
        first_results = [invoke() for _ in range(capacity)]
        assert started.wait(1)
        began = time.monotonic()
        for _ in range(100):
            result = invoke()
            if kind == "read":
                _unknown(result)
        assert time.monotonic() - began < 0.5
        assert len(calls) == capacity
    finally:
        finish.set()
    for _ in range(capacity):
        assert slots.acquire(timeout=1)
    assert len(calls) == capacity
    if kind == "read":
        # Late successful collection must not rewrite a returned unknown result.
        _unknown(first_results[0])
    for _ in range(capacity):
        slots.release()
    invoke()
    for _ in range(capacity):
        assert slots.acquire(timeout=1)
    assert len(calls) == capacity + 1
    for _ in range(capacity):
        slots.release()


@pytest.mark.parametrize("error", [redis.RedisError, OSError, ValueError, RuntimeError])
def test_metric_io_failure_preserves_unknown_and_releases_capacity(monkeypatch, error):
    def fail(*_args):
        raise error("synthetic telemetry failure")

    monkeypatch.setattr(runtime_metrics, "_write_runtime_event", fail)
    monkeypatch.setattr(runtime_metrics, "_read_runtime_events", fail)
    runtime_metrics.record_runtime_event("database_deadline")
    _unknown(runtime_metrics.collect_runtime_events())


def test_metric_thread_start_failure_does_not_escape_or_leak_capacity(monkeypatch):
    def fail_start(_thread):
        raise RuntimeError("synthetic thread exhaustion")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    runtime_metrics.record_runtime_event("outbound_deadline")
    _unknown(runtime_metrics.collect_runtime_events())


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_metric_workers_do_not_inherit_occupied_slots_or_redis_clients(metric_io):
    for slots, capacity in metric_io.values():
        for _ in range(capacity):
            assert slots.acquire(blocking=False)
    runtime_metrics._client("redis://metric-test.invalid:6379/0")
    assert runtime_metrics._client.cache_info().currsize == 1
    receiver, sender = multiprocessing.get_context("fork").Pipe(duplex=False)

    def child():
        sender.send((
            runtime_metrics._delivery_slots.acquire(blocking=False),
            runtime_metrics._delivery_slots.acquire(blocking=False),
            runtime_metrics._collection_slots.acquire(blocking=False),
            runtime_metrics._client.cache_info().currsize,
        ))
        sender.close()

    process = multiprocessing.get_context("fork").Process(target=child)
    try:
        process.start()
        assert receiver.poll(2)
        assert receiver.recv() == (True, True, True, 0)
        process.join(2)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        receiver.close()
        sender.close()
        for slots, capacity in metric_io.values():
            for _ in range(capacity):
                slots.release()
