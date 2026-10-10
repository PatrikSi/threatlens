"""Observer privacy, transaction isolation, bounds and fail-closed controls."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "scripts/operations"
sys.path.insert(0, str(DIRECTORY))
import capacity_diagnostics as diagnostics  # noqa: E402


class Clock:
    value = 0

    def __call__(self):
        return self.value


class ObserverCursor:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.closed, self.executed = rows, error, False, []

    def execute(self, statement):
        self.executed.append(statement)
        if self.error:
            raise self.error

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class SnapshotResult:
    def __init__(self, values=(), *, fields=1, format=0, oid=1700, status=2):
        self.values, self.nfields = list(values), fields
        self.format, self.oid, self.status = format, oid, status
        self.ntuples = len(self.values)
        self.reads = []

    def fformat(self, column):
        return self.format

    def ftype(self, column):
        return self.oid

    def get_value(self, row, column):
        self.reads.append((row, column))
        return self.values[row]


class OriginalCursor:
    def __init__(self, observer, values=()):
        self.connection = types.SimpleNamespace(cursor=lambda: observer)
        self.pgresult = SnapshotResult(values)
        self.position, self.fetch_calls = 0, 0

    def fetchall(self):
        self.fetch_calls += 1
        values = self.pgresult.values[self.position:]
        self.position = self.pgresult.ntuples
        return [(float(value),) for value in values]


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.recorder = diagnostics.Recorder(clock=self.clock)
        self.driver = types.SimpleNamespace(info=types.SimpleNamespace(backend_pid=976123456))
        self.connection = types.SimpleNamespace(connection=types.SimpleNamespace(driver_connection=self.driver))

    def test_exact_policy_categories_exclude_literals_comments_and_similar_tables(self):
        examples = {
            'SELECT * FROM "public"."data_policy_state" FOR UPDATE': "policy_exclusive",
            "SELECT revision FROM data_policy_state FOR SHARE": "policy_shared",
            "SELECT revision FROM data_policy_state FOR KEY SHARE": "other",
            "SELECT * FROM handling_labels FOR UPDATE": "label_exclusive",
            "SELECT * FROM iam_policy_state FOR SHARE": "iam_shared",
            "UPDATE data_policy_state SET revision=revision+1": "policy_update",
            "SELECT 'FROM data_policy_state FOR UPDATE'": "other",
            "SELECT E'escaped\\' FROM data_policy_state FOR UPDATE'": "other",
            "SELECT $tag$FROM data_policy_state FOR UPDATE$tag$": "other",
            'SELECT 1 AS "FROM data_policy_state FOR UPDATE"': "other",
            "SELECT 1 /* outer /* inner */ FROM data_policy_state FOR UPDATE */": "other",
            "SELECT 1 -- FROM data_policy_state FOR UPDATE\n": "other",
            "SELECT * FROM data_policy_state_backup FOR UPDATE": "other",
        }
        for statement, expected in examples.items():
            with self.subTest(statement=statement):
                self.assertEqual(diagnostics.classify_sql(statement), expected)

    def test_only_structural_sampler_select_is_recognized(self):
        valid = "SELECT clock_timestamp() - query_start FROM pg_stat_activity WHERE wait_event_type='Lock'"
        self.assertTrue(diagnostics.is_sampler_sql(valid))
        for fake in [f"SELECT '{valid.replace(chr(39), chr(39) * 2)}'", f"SELECT 1 /* {valid} */", f"SELECT $$ {valid} $$", "SELECT clock_timestamp() - query_start FROM unrelated WHERE wait_event_type='Lock'"]:
            self.assertFalse(diagnostics.is_sampler_sql(fake))

    def test_large_unrelated_query_bypasses_structural_parser(self):
        statement = "SELECT " + ", ".join(f"items.column_{index}" for index in range(10000)) + " FROM items"
        with patch.object(diagnostics, "structural_sql", side_effect=AssertionError("unrelated SQL parsed")) as parser:
            self.assertEqual(diagnostics.classify_sql(statement), "other")
            parser.assert_not_called()

    def test_query_timings_correlate_lane_and_alias_without_pid_or_sql(self):
        with self.recorder.operation("governance"):
            token = self.recorder.query_start(self.connection, "SELECT revision FROM data_policy_state FOR UPDATE")
            self.clock.value = 12_000
            self.recorder.query_end(token)
        record = self.recorder.data["queries"][0]
        self.assertEqual(record["lane"], "governance")
        self.assertEqual(record["end_ns"] - record["start_ns"], 12_000)
        self.assertEqual(record["connection_id"], 1)
        text = json.dumps(self.recorder.data)
        self.assertNotIn("976123456", text)
        self.assertNotIn("SELECT", text)

    def test_failed_query_never_records_successful_fence(self):
        token = self.recorder.query_start(self.connection, "SELECT revision FROM data_policy_state FOR SHARE")
        self.recorder.query_end(token, succeeded=False)
        self.assertEqual(self.recorder.fences, {})
        self.assertEqual(self.recorder.data["queries"][0]["status"], "failed")

    def test_workload_query_failure_counts_are_bounded_and_retain_no_message(self):
        for _ in range(2):
            self.recorder.workload_query_failed(RuntimeError("private SQL parameters"))
        self.assertEqual(self.recorder.workload_query_failures, {"RuntimeError": 2})
        for index in range(31):
            self.recorder.workload_query_failed(type(f"DatabaseError{index}", (Exception,), {})())
        with self.assertRaises(ValueError):
            self.recorder.workload_query_failed(TypeError("private credential"))
        self.assertEqual(len(self.recorder.workload_query_failures), 32)
        self.assertNotIn("private", json.dumps(self.recorder.workload_query_failures))

    def test_fence_history_retires_on_pool_return_or_next_begin(self):
        for retirement in ["pool_returned", "begin"]:
            token = self.recorder.query_start(self.connection, "SELECT revision FROM data_policy_state FOR SHARE")
            self.recorder.query_end(token)
            self.recorder.transaction(self.connection, "commit_requested")
            self.assertEqual(self.recorder.fences[1], {"policy_shared"})
            self.assertIn(1, self.recorder.ending)
            self.recorder.transaction(self.connection, retirement)
            self.assertNotIn(1, self.recorder.fences)
            self.assertNotIn(1, self.recorder.ending)

    def test_uninteresting_sampler_commits_do_not_exhaust_transaction_bound(self):
        for _ in range(30000):
            self.recorder.transaction(self.connection, "commit_requested")
        self.assertEqual(self.recorder.data["transactions"], [])
        self.assertFalse(any(self.recorder.dropped.values()))

    def sample(self, observer):
        original = OriginalCursor(observer)
        with patch.object(diagnostics, "host_sample", return_value={"sample_ns": self.clock.value}):
            self.recorder.sampler(original)

    def test_original_sampler_snapshot_preserves_fetch_position_and_result(self):
        original = OriginalCursor(ObserverCursor(), [b"123.456789", b"0"])
        result = original.pgresult
        with patch.object(diagnostics, "host_sample", return_value={}):
            self.recorder.sampler(original)
        self.assertIs(original.pgresult, result)
        self.assertEqual(original.position, 0)
        self.assertEqual(original.fetch_calls, 0)
        self.assertEqual(original.fetchall(), [(123.456789,), (0.0,)])
        self.assertEqual(result.reads, [(0, 0), (1, 0)])
        self.assertEqual(self.recorder.data["original_sampler_samples"][0]["query_ages_ms"], [123.456789, 0.0])
        self.assertNotIn("SELECT", json.dumps(self.recorder.data))

    def test_original_sampler_snapshot_records_empty_samples_and_monotonic_time(self):
        original = OriginalCursor(ObserverCursor())
        with patch.object(diagnostics, "host_sample", return_value={}):
            self.recorder.sampler(original)
            self.clock.value = 20_000_000
            self.recorder.sampler(original)
        rows = self.recorder.data["original_sampler_samples"]
        self.assertEqual([row["sample_ns"] for row in rows], [0, 20_000_000])
        self.assertEqual([row["query_ages_ms"] for row in rows], [[], []])
        self.assertEqual(original.fetch_calls, 0)

    def test_new_original_peak_triggers_bounded_graph_without_shifting_periodic_schedule(self):
        observers = []
        for at, age in [(0, b"100"), (20_000_000, b"200"), (40_000_000, b"200"), (80_000_000, b"150"), (100_000_000, b"0")]:
            self.clock.value = at
            observer = ObserverCursor()
            observers.append(observer)
            with patch.object(diagnostics, "host_sample", return_value={}):
                self.recorder.sampler(OriginalCursor(observer, [age]))
        self.assertEqual([bool(value.executed) for value in observers], [True, True, False, False, True])
        rows = self.recorder.data["locks"]
        self.assertEqual([row["trigger"] for row in rows], ["new_original_query_age_peak", "new_original_query_age_peak", "periodic"])
        self.assertEqual([row["original_sample_index"] for row in rows], [0, 1, 4])
        self.assertEqual([row["original_sample_peak_ms"] for row in rows], [100.0, 200.0, 0.0])
        self.assertTrue(all(row["sample_ns"] <= row["observer_query_start_ns"] <= row["observer_query_end_ns"] for row in rows))

    def test_zero_age_is_not_new_peak_and_graph_overflow_is_failed_capture(self):
        self.recorder.limit["locks"] = 1
        for at, age in [(0, b"0"), (20_000_000, b"0"), (40_000_000, b"1")]:
            self.clock.value = at
            with patch.object(diagnostics, "host_sample", return_value={}):
                self.recorder.sampler(OriginalCursor(ObserverCursor(), [age]))
        self.assertEqual(self.recorder.data["locks"][0]["trigger"], "periodic")
        self.assertEqual(self.recorder.dropped["locks"], 1)
        self.assertEqual(self.recorder.finish(1)["status"], "failed")

    def test_original_sampler_snapshot_rejects_unexpected_shape_or_private_values(self):
        variants = [SnapshotResult(fields=2), SnapshotResult(format=1), SnapshotResult(oid=25), SnapshotResult(status=1), None,
                    SnapshotResult([None]), SnapshotResult([b"private SQL credential"]), SnapshotResult([b"NaN"]),
                    SnapshotResult([b"Infinity"]), SnapshotResult([b"-1"]), SnapshotResult([b"1" * 65])]
        for result in variants:
            with self.subTest(result=result):
                recorder = diagnostics.Recorder(clock=self.clock)
                original = OriginalCursor(ObserverCursor())
                original.pgresult = result
                with patch.object(diagnostics, "host_sample", return_value={}):
                    recorder.sampler(original)
                self.assertTrue(any(row["phase"] == "original_sampler_snapshot" for row in recorder.errors))
                self.assertEqual(original.fetch_calls, 0)
                self.assertNotIn("private", json.dumps(recorder.finish(1)))

    def test_original_sampler_row_and_event_bounds_fail_capture(self):
        original = OriginalCursor(ObserverCursor(), [b"1"] * 65)
        with patch.object(diagnostics, "host_sample", return_value={}):
            self.recorder.sampler(original)
        self.assertTrue(any(row["phase"] == "original_sampler_snapshot" for row in self.recorder.errors))
        self.recorder.limit["original_sampler_samples"] = 1
        original.pgresult = SnapshotResult()
        with patch.object(diagnostics, "host_sample", return_value={}):
            self.recorder.sampler(original)
            self.recorder.sampler(original)
        self.assertEqual(self.recorder.dropped["original_sampler_samples"], 1)
        self.assertEqual(self.recorder.finish(1)["status"], "failed")

    def test_original_sampler_peak_counts_reconcile_with_unchanged_measurement(self):
        original = OriginalCursor(ObserverCursor(), [b"123.456789", b"0"])
        with patch.object(diagnostics, "host_sample", return_value={}):
            self.recorder.sampler(original)
        self.recorder.add("operations", {"lane": "governance"})
        self.recorder.query_end(self.recorder.query_start(self.connection, "SELECT * FROM data_policy_state"))
        for lane in ["governance", "ai_connection", "export", "feed", "repair"]:
            self.recorder.add("lanes", {"lane": lane})
        measurement = {"sampler": {"samples": 1}, "database": {"sampled_lock_waiting_query_age_peak_ms": 123.457, "lock_wait_samples": 2, "waiting_sessions_peak": 2}}
        record = self.recorder.finish(1, measurement=measurement)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["coverage"]["original_sampler_samples"], 1)
        self.assertEqual(record["sampler"]["original_query_age_peak_ms"], 123.457)
        self.assertEqual(record["sampler"]["original_lock_wait_samples"], 2)
        self.assertEqual(record["sampler"]["original_waiting_sessions_peak"], 2)
        for group, key, invalid in [("sampler", "samples", 2), ("sampler", "samples", True),
                                    ("database", "sampled_lock_waiting_query_age_peak_ms", 123.456),
                                    ("database", "sampled_lock_waiting_query_age_peak_ms", float("nan")),
                                    ("database", "sampled_lock_waiting_query_age_peak_ms", True),
                                    ("database", "lock_wait_samples", 1), ("database", "waiting_sessions_peak", 1),
                                    ("database", "lock_wait_samples", True)]:
            with self.subTest(group=group, key=key, invalid=invalid):
                self.recorder.errors.clear()
                bad = json.loads(json.dumps(measurement))
                bad[group][key] = invalid
                failed = self.recorder.finish(1, measurement=bad)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["errors"], [{"phase": "original_sampler_link", "error_type": "ValueError"}])

    def test_waiter_current_lock_wait_age_preserves_null_zero_and_distinct_query_age(self):
        rows = [(976123456 + index, "SELECT 1", "transactionid", [], 1000.0, 2000.0, value)
                for index, value in enumerate([None, 0, 25.125])]
        self.sample(ObserverCursor(rows))
        values = self.recorder.data["locks"][0]["connections"]
        self.assertEqual([row["current_lock_wait_age_ms"] for row in values], [None, 0.0, 25.125])
        self.assertEqual([row["query_age_ms"] for row in values], [1000.0] * 3)
        self.assertIn("pg_locks", diagnostics.WAIT_SQL)
        self.assertIn("waitstart", diagnostics.WAIT_SQL)
        self.assertIn("NOT granted", diagnostics.WAIT_SQL)

    def test_waiter_current_lock_wait_age_rejects_bad_shape_and_nonfinite_values(self):
        for row in [(976123456, "private SQL credential", "transactionid", [], 1, 2),
                    (976123456, "private SQL credential", "transactionid", [], 1, 2, float("nan")),
                    (976123456, "private SQL credential", "transactionid", [], 1, 2, -1)]:
            with self.subTest(size=len(row)):
                recorder = diagnostics.Recorder(clock=self.clock)
                observer = ObserverCursor([row])
                with patch.object(diagnostics, "host_sample", return_value={}):
                    recorder.sampler(OriginalCursor(observer))
                self.assertEqual(recorder.errors[0]["phase"], "waiter_observer")
                self.assertTrue(observer.closed)
                self.assertNotIn("private", json.dumps(recorder.finish(1)))

    def test_separate_sampler_cursor_closes_and_exposes_only_numeric_categories(self):
        rows = [(976123456, "SELECT * FROM data_policy_state FOR UPDATE", "transactionid", [976123457], 125.0, 200.0, 50.0), (976123457, "SELECT 'private-content'", None, None, 3.0, 180.0, None)]
        observer = ObserverCursor(rows)
        self.sample(observer)
        self.assertTrue(observer.closed)
        self.assertEqual(observer.executed, [diagnostics.WAIT_SQL])
        self.assertEqual(self.recorder.data["locks"][0]["connections"][0]["blocker_ids"], [2])
        serialized = json.dumps(self.recorder.data)
        for private in ["976123456", "976123457", "private-content", "SELECT"]:
            self.assertNotIn(private, serialized)

    def test_sampler_decimation_preserves_original_calls_and_records_actual_cadence(self):
        first, skipped, second = ObserverCursor(), ObserverCursor(), ObserverCursor()
        self.sample(first)
        self.clock.value = 99_000_000
        self.sample(skipped)
        self.clock.value = 100_000_000
        self.sample(second)
        self.assertEqual(skipped.executed, [])
        self.assertEqual(len(self.recorder.data["locks"]), 2)
        self.assertEqual(self.recorder.sampler_count, 3)
        self.assertEqual(self.recorder.sampler_gap_max, 99_000_000)

    def test_fence_snapshot_is_locked_but_observer_io_is_unlocked(self):
        recorder = self.recorder

        class CheckedSet(set):
            def __iter__(self):
                if not recorder.lock._is_owned():
                    raise RuntimeError("unguarded mutable fence iteration")
                return super().__iter__()

        class CheckedCursor(ObserverCursor):
            def execute(self, statement):
                if recorder.lock._is_owned():
                    raise RuntimeError("lock held during observer I/O")
                return super().execute(statement)

        recorder.fences[1] = CheckedSet(["policy_shared"])
        recorder.alias(976123456)
        cursor = CheckedCursor([(976123456, "SELECT 1", None, None, 1.0, 1.0, None)])
        self.sample(cursor)
        self.assertEqual(recorder.errors, [])
        self.assertEqual(recorder.data["locks"][0]["connections"][0]["observed_fences"], ["policy_shared"])

    def test_observer_failure_closes_cursor_and_redacts_exception_message(self):
        observer = ObserverCursor(error=RuntimeError("secret SQL credential"))
        self.sample(observer)
        self.assertTrue(observer.closed)
        self.assertEqual(self.recorder.errors, [{"phase": "waiter_observer", "error_type": "RuntimeError"}])
        self.assertNotIn("secret", json.dumps(self.recorder.finish(1)))

    def test_event_overflow_and_incomplete_capture_cannot_be_green(self):
        self.recorder.limit["queries"] = 1
        for _ in range(2):
            self.recorder.query_end(self.recorder.query_start(self.connection, "SELECT * FROM data_policy_state"))
        result = self.recorder.finish(300)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["dropped_events"]["queries"], 1)
        self.assertEqual(result["coverage"]["governance_operations"], 0)

    def test_lane_observer_preserves_arguments_counts_and_operation_results(self):
        seen = []
        def original(operation, **kwargs):
            seen.append(kwargs)
            return operation(7)
        kwargs = {"duration_seconds": 600, "interval_seconds": 2, "initial_delay_seconds": 0.5}
        result = self.recorder.lane(original, lambda index: index * 3, **kwargs)
        self.assertEqual(result, 21)
        self.assertEqual(seen, [kwargs])
        self.assertEqual(self.recorder.local.index, 7)
        self.assertEqual(self.recorder.data["lanes"][0]["lane"], "governance")


class PluginTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("isolated_capacity_plugin", DIRECTORY / "capacity_diagnostics_plugin.py")
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        digest, _ = diagnostics.observer_identity(DIRECTORY)
        self.environment = {"THREATLENS_CAPACITY_PROFILE": "sustained", "THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT": str(self.directory / "diagnostics.json"), "THREATLENS_CAPACITY_OUTPUT": str(self.directory / "measurement.json"), "THREATLENS_CAPACITY_DIAGNOSTICS_CONTRACT": diagnostics.CONTRACT, "THREATLENS_CAPACITY_DIAGNOSTICS_OBSERVER_SHA256": digest, "THREATLENS_CAPACITY_RUN_ID": "a" * 32, "THREATLENS_CAPACITY_SOURCE_REVISION": "b" * 40}
        (self.directory / "measurement.json").write_text(json.dumps({"run_id": "a" * 32, "git_revision": "b" * 40, "workload_completed": {"governance": 1}, "sampler": {"samples": 1}, "database": {"sampled_lock_waiting_query_age_peak_ms": 0.0, "lock_wait_samples": 0, "waiting_sessions_peak": 0}}))

    def populate(self):
        recorder = self.plugin._recorder
        recorder.add("operations", {"lane": "governance"})
        recorder.add("queries", {})
        recorder.add("locks", {})
        recorder.add("host", {})
        recorder.add("original_sampler_samples", {"sample_ns": 0, "snapshot_elapsed_ns": 0, "query_ages_ms": []})
        recorder.sampler_count = 1
        for lane in ["governance", "ai_connection", "export", "feed", "repair"]:
            recorder.add("lanes", {"lane": lane})

    def finish(self, exitcode=0):
        session = types.SimpleNamespace(exitstatus=exitcode)
        self.plugin.pytest_sessionfinish(session, exitcode)
        return session, json.loads((self.directory / "diagnostics.json").read_text())

    def test_plugin_is_inert_without_explicit_output(self):
        with patch.dict(os.environ, {}, clear=True):
            self.plugin.pytest_configure(None)
        self.assertIsNone(self.plugin._recorder)

    def test_complete_linked_capture_and_current_observer_identity_pass(self):
        with patch.dict(os.environ, self.environment, clear=True):
            self.plugin.pytest_configure(None)
            self.populate()
            session, record = self.finish()
        self.assertEqual(session.exitstatus, 0)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["workload_exit_code"], 0)

    def test_original_sampler_measurement_mismatch_fails_successful_workload(self):
        measured = json.loads((self.directory / "measurement.json").read_text())
        measured["database"]["sampled_lock_waiting_query_age_peak_ms"] = 1.0
        (self.directory / "measurement.json").write_text(json.dumps(measured))
        with patch.dict(os.environ, self.environment, clear=True):
            self.plugin.pytest_configure(None)
            self.populate()
            session, record = self.finish()
        self.assertEqual(int(session.exitstatus), 1)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["errors"], [{"phase": "original_sampler_link", "error_type": "ValueError"}])

    def test_observer_failure_turns_successful_workload_exit_nonzero(self):
        with patch.dict(os.environ, self.environment, clear=True):
            self.plugin.pytest_configure(None)
            self.populate()
            self.plugin._recorder.error("waiter_observer", RuntimeError("private SQL"))
            session, record = self.finish()
        self.assertEqual(int(session.exitstatus), 1)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["workload_exit_code"], 0)
        self.assertNotIn("private SQL", json.dumps(record))

    def test_diagnostic_failure_preserves_already_failed_workload_exit(self):
        with patch.dict(os.environ, self.environment, clear=True):
            self.plugin.pytest_configure(None)
            session, record = self.finish(4)
        self.assertEqual(session.exitstatus, 4)
        self.assertEqual(record["workload_exit_code"], 4)
        self.assertEqual(record["status"], "failed")

    def captured_query_failure(self, exitcode=0, *, capture_fault=False):
        @contextmanager
        def operation(self, name):
            yield {}

        class Measurements:
            pass

        Measurements.operation = operation
        Measurements.sample = lambda self, callback: callback()
        capacity = types.ModuleType("tests.capacity")
        capacity.workload_support = types.SimpleNamespace(Measurements=Measurements)
        item = types.SimpleNamespace(module=types.SimpleNamespace(paced_lane=lambda *args, **kwargs: 0))
        try:
            with patch.dict(os.environ, self.environment, clear=True), patch.dict(sys.modules, {"tests.capacity": capacity}):
                self.plugin.pytest_configure(None)
                self.plugin.pytest_runtest_setup(item)
                self.populate()
                recorder = self.plugin._recorder
                driver = types.SimpleNamespace(info=types.SimpleNamespace(backend_pid=976123456))
                token = recorder.query_start(driver, "SELECT revision FROM data_policy_state FOR SHARE")
                context = types.SimpleNamespace(original_exception=RuntimeError("private SQL parameters credential"), execution_context=types.SimpleNamespace(_capacity_observation=token))
                callback = next(value[2] for value in self.plugin._listeners if value[1] == "handle_error")
                if capture_fault:
                    with patch.object(recorder, "workload_query_failed", create=True, side_effect=ValueError("private capture fault")):
                        self.assertIsNone(callback(context))
                else:
                    self.assertIsNone(callback(context))
                return self.finish(exitcode)
        finally:
            from sqlalchemy import event
            for target, name, callback in self.plugin._listeners:
                event.remove(target, name, callback)
            for target, name, original in reversed(self.plugin._patches):
                setattr(target, name, original)
            self.plugin._listeners.clear()
            self.plugin._patches.clear()

    def test_captured_handled_query_failure_keeps_complete_workload_success(self):
        session, record = self.captured_query_failure()
        self.assertEqual(session.exitstatus, 0)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["errors"], [])
        self.assertEqual(record["workload_query_failures"], {"RuntimeError": 1})
        self.assertEqual(record["workload_exit_code"], 0)
        failed = [value for value in record["queries"] if value.get("status") == "failed"]
        self.assertEqual([value["category"] for value in failed], ["policy_shared"])
        self.assertEqual(self.plugin._recorder.fences, {})
        self.assertNotIn("private", json.dumps(record))

    def test_captured_query_failure_preserves_original_workload_failure(self):
        session, record = self.captured_query_failure(1)
        self.assertEqual(session.exitstatus, 1)
        self.assertEqual(record["workload_exit_code"], 1)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["workload_query_failures"], {"RuntimeError": 1})

    def test_query_failure_capture_fault_still_fails_complete_diagnostics(self):
        session, record = self.captured_query_failure(capture_fault=True)
        self.assertEqual(int(session.exitstatus), 1)
        self.assertEqual(record["workload_exit_code"], 0)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["errors"], [{"phase": "workload_query_failure", "error_type": "ValueError"}])
        self.assertTrue(any(value.get("status") == "failed" for value in record["queries"]))
        self.assertNotIn("private", json.dumps(record))

    def test_stale_measurement_or_changed_observer_identity_fails(self):
        for key in ["THREATLENS_CAPACITY_RUN_ID", "THREATLENS_CAPACITY_DIAGNOSTICS_OBSERVER_SHA256"]:
            with self.subTest(key=key), patch.dict(os.environ, {**self.environment, key: "0" * 40}, clear=True):
                self.plugin._recorder = diagnostics.Recorder()
                self.populate()
                session, record = self.finish()
                self.assertEqual(int(session.exitstatus), 1)
                self.assertEqual(record["status"], "failed")

    def test_output_write_failure_fails_without_replacing_workload_failure(self):
        with patch.dict(os.environ, self.environment, clear=True):
            self.plugin.pytest_configure(None)
            self.populate()
            with patch.object(Path, "write_text", side_effect=OSError("private path")):
                session = types.SimpleNamespace(exitstatus=0)
                self.plugin.pytest_sessionfinish(session, 0)
        self.assertEqual(int(session.exitstatus), 1)
        self.assertEqual(self.plugin._recorder.errors[-1], {"phase": "output_write", "error_type": "OSError"})

    def test_only_actual_sampler_context_can_run_observer_select(self):
        @contextmanager
        def operation(self, name):
            yield {}

        class Measurements:
            pass

        Measurements.operation = operation
        Measurements.sample = lambda self, callback: callback()
        capacity = types.ModuleType("tests.capacity")
        capacity.workload_support = types.SimpleNamespace(Measurements=Measurements)
        item = types.SimpleNamespace(module=types.SimpleNamespace(paced_lane=lambda *args, **kwargs: 0))
        statement = "SELECT clock_timestamp() - query_start FROM pg_stat_activity WHERE wait_event_type='Lock'"
        try:
            with patch.dict(os.environ, self.environment, clear=True), patch.dict(sys.modules, {"tests.capacity": capacity}):
                self.plugin.pytest_configure(None)
                self.plugin.pytest_runtest_setup(item)
                callback = next(value[2] for value in self.plugin._listeners if value[1] == "after_cursor_execute")
                context = types.SimpleNamespace(_capacity_observation=None)
                with patch.object(self.plugin._recorder, "sampler") as sampler:
                    def invoke(value):
                        callback(None, object(), value, {}, context, False)
                    invoke(statement)
                    sampler.assert_not_called()
                    Measurements().sample(lambda: invoke("SELECT $$" + statement + "$$"))
                    sampler.assert_not_called()
                    Measurements().sample(lambda: invoke(statement))
                    sampler.assert_called_once()
                    self.assertFalse(self.plugin._recorder.local.sampler)
        finally:
            from sqlalchemy import event
            for target, name, callback in self.plugin._listeners:
                event.remove(target, name, callback)
            for target, name, original in reversed(self.plugin._patches):
                setattr(target, name, original)
            self.plugin._listeners.clear()
            self.plugin._patches.clear()


if __name__ == "__main__":
    unittest.main()
