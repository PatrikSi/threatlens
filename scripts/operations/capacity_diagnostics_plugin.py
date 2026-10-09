"""Explicit pytest observer, supplied identically outside frozen workload trees."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from capacity_diagnostics import CONTRACT, Recorder, is_sampler_sql, observer_identity

_recorder = None
_patches = []
_listeners = []


def pytest_configure(config):
    global _recorder
    output = os.environ.get("THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT")
    if not output:
        return
    if os.environ.get("THREATLENS_CAPACITY_PROFILE") != "sustained":
        raise pytest.UsageError("capacity diagnostics require sustained profile")
    if Path(output).exists():
        raise pytest.UsageError("preserve previous capacity diagnostics; choose a fresh output")
    _recorder = Recorder()


@pytest.hookimpl(trylast=True)
def pytest_runtest_setup(item):
    if _recorder is None or _patches:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.pool import Pool
    from tests.capacity import workload_support

    original = workload_support.Measurements.operation

    @contextmanager
    def operation(measurements, name):
        with _recorder.operation(name), original(measurements, name) as value:
            yield value

    workload_support.Measurements.operation = operation
    _patches.append((workload_support.Measurements, "operation", original))
    original_sample = workload_support.Measurements.sample

    def sample(measurements, *arguments):
        previous = getattr(_recorder.local, "sampler", False)
        _recorder.local.sampler = True
        try:
            return original_sample(measurements, *arguments)
        finally:
            _recorder.local.sampler = previous

    workload_support.Measurements.sample = sample
    _patches.append((workload_support.Measurements, "sample", original_sample))
    original_lane = item.module.paced_lane

    def lane(operation, **kwargs):
        return _recorder.lane(original_lane, operation, **kwargs)

    item.module.paced_lane = lane
    _patches.append((item.module, "paced_lane", original_lane))

    def guarded(phase, operation):
        start = _recorder.clock()
        try:
            return operation()
        except Exception as error:
            _recorder.error(phase, error)
            return None
        finally:
            _recorder.observe_overhead(phase, _recorder.clock() - start)

    def before(connection, cursor, statement, parameters, context, executemany):
        context._capacity_observation = guarded("query_before", lambda: _recorder.query_start(connection, statement))

    def after(connection, cursor, statement, parameters, context, executemany):
        guarded("query_after", lambda: _recorder.query_end(getattr(context, "_capacity_observation", None)))
        if getattr(_recorder.local, "sampler", False) and is_sampler_sql(statement):
            guarded("sampler", lambda: _recorder.sampler(cursor))

    def commit(connection):
        guarded("transaction_commit", lambda: _recorder.transaction(connection, "commit_requested"))

    def begin(connection):
        guarded("transaction_begin", lambda: _recorder.transaction(connection, "begin"))

    def rollback(connection):
        guarded("transaction_rollback", lambda: _recorder.transaction(connection, "rollback_requested"))

    def checkin(driver, record):
        if driver is not None:
            guarded("pool_return", lambda: _recorder.transaction(driver, "pool_returned"))

    def failed(context):
        guarded("workload_query_failure", lambda: _recorder.workload_query_failed(context.original_exception))
        guarded("query_failed", lambda: _recorder.query_end(getattr(context.execution_context, "_capacity_observation", None), succeeded=False))

    for target, name, callback in [(Engine, "before_cursor_execute", before), (Engine, "after_cursor_execute", after), (Engine, "handle_error", failed), (Engine, "begin", begin), (Engine, "commit", commit), (Engine, "rollback", rollback), (Pool, "checkin", checkin)]:
        event.listen(target, name, callback)
        _listeners.append((target, name, callback))


def pytest_sessionfinish(session, exitstatus):
    if _recorder is None:
        return
    from sqlalchemy import event

    for target, name, callback in _listeners:
        try:
            event.remove(target, name, callback)
        except Exception as error:
            _recorder.error("listener_remove", error)
    _listeners.clear()
    for target, name, original in reversed(_patches):
        setattr(target, name, original)
    _patches.clear()
    measurement = None
    try:
        measurement = json.loads(Path(os.environ["THREATLENS_CAPACITY_OUTPUT"]).read_text())
        if measurement["run_id"] != os.environ.get("THREATLENS_CAPACITY_RUN_ID") or measurement["git_revision"] != os.environ.get("THREATLENS_CAPACITY_SOURCE_REVISION"):
            raise ValueError("measurement identity")
        expected = measurement["workload_completed"]["governance"]
    except Exception as error:
        _recorder.error("measurement_link", error)
        expected = 0
    try:
        digest, hashes = observer_identity(Path(__file__).parent)
    except Exception as error:
        _recorder.error("observer_files", error)
        digest, hashes = None, {}
    if digest != os.environ.get("THREATLENS_CAPACITY_DIAGNOSTICS_OBSERVER_SHA256") or CONTRACT != os.environ.get("THREATLENS_CAPACITY_DIAGNOSTICS_CONTRACT"):
        _recorder.error("observer_identity", ValueError())
    result = _recorder.finish(expected, measurement=measurement)
    result.update(schema_version=1, contract=CONTRACT, observer_sha256=digest, observer_file_sha256=hashes, capacity_run_id=os.environ.get("THREATLENS_CAPACITY_RUN_ID"), application_source_revision=os.environ.get("THREATLENS_CAPACITY_SOURCE_REVISION"), workload_exit_code=int(exitstatus), observation_scope="Opt-in diagnostic perturbation; original workload/metrics/comparator unchanged")
    try:
        path = Path(os.environ["THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    except Exception as error:
        _recorder.error("output_write", error)
        result["status"] = "failed"
    if result["status"] != "passed" and not exitstatus:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
