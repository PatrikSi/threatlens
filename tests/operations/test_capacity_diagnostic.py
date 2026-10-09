"""Opt-in observers must not replace frozen workload controls or identities."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
REVISION = "c" * 40
RUN_ID = "a" * 32


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapacityDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_module(ROOT / "scripts/operations/run_capacity_diagnostic.py", "diagnostic_runner")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.observer = self.directory / "observer"
        self.observer.mkdir()
        for name in self.runner.OBSERVER_FILES:
            (self.observer / name).write_text(f"# Frozen observer {name}\n")
        self.identity = self.directory / "identity.json"
        self.identity.write_text(json.dumps(self.runner.observer_identity(self.observer)))
        self.measurement = self.directory / "measurement.json"
        self.measurement.write_text(json.dumps({"git_revision": REVISION, "run_id": RUN_ID,
                                               "status": "passed",
                                               "sampler": {"samples": 3, "errors": []},
                                               "database": {"sampled_lock_waiting_query_age_peak_ms": 3.457,
                                                            "lock_wait_samples": 3, "waiting_sessions_peak": 2},
                                               "workload_completed": {"governance": 2}}))
        self.output = self.directory / "diagnostics.json"
        self.record = {**self.runner.observer_identity(self.observer),
                       "capacity_run_id": RUN_ID, "application_source_revision": REVISION,
                       "workload_exit_code": 0,
                       "status": "passed", "errors": [], "dropped_events": {"queries": 0, "original_sampler_samples": 0},
                       "coverage": {"governance_operations": 2, "operation_events": 2,
                                    "query_events": 1, "lock_samples": 3, "host_samples": 1, "lane_starts": 5,
                                    "original_sampler_samples": 3},
                       "sampler": {"observations": 3, "gap_max_ns": 100, "gap_mean_ns": 100,
                                   "original_query_age_peak_ms": 3.457, "original_lock_wait_samples": 3,
                                   "original_waiting_sessions_peak": 2},
                       "original_sampler_samples": [
                           {"index": 0, "sample_ns": 100, "snapshot_elapsed_ns": 1, "query_ages_ms": []},
                           {"index": 1, "sample_ns": 200, "snapshot_elapsed_ns": 1, "query_ages_ms": [1.23456, 2.34567]},
                           {"index": 2, "sample_ns": 300, "snapshot_elapsed_ns": 1, "query_ages_ms": [3.45678]},
                       ],
                       "connection_alias_count": 1,
                       "operations": [{"lane": "governance"}, {"lane": "governance"}],
                       "lanes": [{"lane": name} for name in ("governance", "ai_connection", "export", "feed", "repair")],
                       "queries": [{}], "locks": [self.graph(index) for index in range(3)], "host": [{}]}

    @staticmethod
    def graph(index):
        sample_ns = (index + 1) * 100
        return {"sample_ns": sample_ns, "observer_elapsed_ns": 10,
                "observer_query_start_ns": sample_ns + 2, "observer_query_end_ns": sample_ns + 8,
                "trigger": "periodic" if index == 0 else "new_original_query_age_peak",
                "original_sample_index": index, "original_sample_peak_ms": (0, 2.34567, 3.45678)[index],
                "connections": [] if index == 0 else [{
                    "connection_id": 1, "category": "policy_shared", "wait_category": "transactionid",
                    "blocker_ids": [0, 1], "is_waiter": True, "query_age_ms": 2.5,
                    "transaction_age_ms": 4.0, "current_lock_wait_age_ms": None,
                    "observed_fences": ["policy_shared"], "transaction_end_requested": False,
                }]}

    def verify(self, record=None):
        self.output.write_text(json.dumps(self.record if record is None else record))
        return self.runner.verify_reference(observer=self.observer, identity=self.identity,
            measurement=self.measurement, diagnostic=self.output, source_revision=REVISION)

    def launch(self, *, diagnostic=True, exit_code=0, raises=False):
        captured = []

        def execute(command, **kwargs):
            captured.append((command, kwargs))
            if raises:
                raise RuntimeError("private failure")
            kwargs["output"].write_text(json.dumps({"run_id": kwargs["run_id"]}))
            return exit_code

        process = types.ModuleType("capacity_process")
        process.execute_bounded = execute
        process.require_local_docker = lambda: {"DOCKER_HOST": "unix:///var/run/docker.sock"}
        entrypoint = ROOT / "backend/scripts/run_capacity_baseline.py"
        arguments = ["--profile", "sustained", "--duration-seconds", "600", "--target-id", "fixture",
                     "--cpu-count", "1", "--max-rss-mib", "1024", "--output", str(self.measurement)]
        ambient = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(self.observer),
                   "PYTEST_PLUGINS": "capacity_diagnostics_plugin",
                   "THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT": str(self.output)}
        previous_argv = sys.argv[:]
        try:
            with patch.dict(sys.modules, {"capacity_process": process}), patch.dict(os.environ, ambient, clear=True), patch(
                "subprocess.check_output", side_effect=lambda command, **_: REVISION + "\n" if command[1] == "rev-parse" else "",
            ), patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)), patch("signal.signal"):
                if diagnostic:
                    code = self.runner.run_supervisor(entrypoint=entrypoint, observer=self.observer,
                                                      output=self.output, arguments=arguments)
                else:
                    supervisor = load_module(entrypoint, "unwrapped_frozen_supervisor")
                    sys.argv = [str(entrypoint), *arguments]
                    code = supervisor.main()
        finally:
            sys.argv[:] = previous_argv
        self.assertIs(process.execute_bounded, execute)
        return code, captured

    def test_ambient_plugin_configuration_is_scrubbed_by_unchanged_supervisor(self):
        code, calls = self.launch(diagnostic=False)
        self.assertEqual(code, 0)
        command, kwargs = calls[0]
        self.assertNotIn("-p", command)
        for name in ("PYTHONPATH", "PYTEST_PLUGINS", "THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT"):
            self.assertNotIn(name, kwargs["env"])

    def test_plugin_is_injected_after_scrubbing_without_changing_controls(self):
        _, original_calls = self.launch(diagnostic=False)
        original_command, original = original_calls[0]
        code, observed_calls = self.launch()
        command, observed = observed_calls[0]
        self.assertEqual(code, 0)
        self.assertEqual(len(observed_calls), 1)
        self.assertEqual(command, [*original_command[:3], "-p", "capacity_diagnostics_plugin", *original_command[3:]])
        self.assertEqual(observed["limits"], original["limits"])
        self.assertEqual(observed["limits"]["wall_timeout_seconds"], 840)
        self.assertEqual(observed["limits"]["max_rss_bytes"], 1024 * 1024 * 1024)
        self.assertEqual(observed["env"]["THREATLENS_CAPACITY_DURATION"], "600")
        self.assertEqual(observed["env"]["THREATLENS_CAPACITY_DIAGNOSTICS_OUTPUT"], str(self.output))
        self.assertEqual(observed["env"]["PYTHONPATH"], str(self.observer))
        self.assertEqual(observed["env"]["THREATLENS_CAPACITY_DIAGNOSTICS_OBSERVER_SHA256"],
                         self.runner.observer_identity(self.observer)["observer_sha256"])
        for name, value in original["env"].items():
            if name != "THREATLENS_CAPACITY_RUN_ID" and name != "THREATLENS_CAPACITY_CONTAINER_MANIFEST":
                self.assertEqual(observed["env"][name], value)
        self.assertNotIn("PYTEST_PLUGINS", observed["env"])

    def test_workload_failure_codes_are_preserved(self):
        for code in (3, 124, 143):
            with self.subTest(code=code):
                actual, _ = self.launch(exit_code=code)
                self.assertEqual(actual, code)

    def test_workload_exception_restores_original_loader_and_arguments(self):
        before_path, before_argv = sys.path[:], sys.argv[:]
        with self.assertRaisesRegex(RuntimeError, "private failure"):
            self.launch(raises=True)
        self.assertEqual(sys.path, before_path)
        self.assertEqual(sys.argv, before_argv)

    def test_external_observer_is_required(self):
        with self.assertRaisesRegex(ValueError, "external"):
            self.runner.run_supervisor(entrypoint=self.observer / "scripts/fixture.py",
                observer=self.observer, output=self.output, arguments=[])

    def test_observer_hash_is_canonical_and_both_files_are_bound(self):
        identity = self.runner.observer_identity(self.observer)
        encoded = json.dumps(identity["observer_file_sha256"], sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(identity["observer_sha256"], hashlib.sha256(encoded).hexdigest())
        for name in self.runner.OBSERVER_FILES:
            original = (self.observer / name).read_bytes()
            (self.observer / name).write_bytes(original + b"# Changed\n")
            self.assertNotEqual(self.runner.observer_identity(self.observer)["observer_sha256"], identity["observer_sha256"])
            (self.observer / name).write_bytes(original)

    def test_complete_capture_verifies_without_changing_any_artifact(self):
        self.verify()
        before = {path: path.read_bytes() for path in (self.identity, self.measurement, self.output)}
        self.runner.verify_reference(observer=self.observer, identity=self.identity,
            measurement=self.measurement, diagnostic=self.output, source_revision=REVISION)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_successful_capture_requires_original_workload_exit_zero(self):
        for value in (1, True, None):
            record = copy.deepcopy(self.record)
            if value is None:
                del record["workload_exit_code"]
            else:
                record["workload_exit_code"] = value
            with self.subTest(workload_exit_code=value), self.assertRaisesRegex(ValueError, "workload"):
                self.verify(record)

    def test_successful_capture_requires_passed_measurement(self):
        record = json.loads(self.measurement.read_text())
        record["status"] = "failed"
        self.measurement.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "Measurement did not pass"):
            self.verify()

    def test_partial_malformed_or_mismatched_capture_fails_closed(self):
        changes = [
            {"schema_version": True}, {"contract": "other"}, {"observer_sha256": "b" * 64},
            {"observer_file_sha256": {}}, {"capacity_run_id": "b" * 32},
            {"application_source_revision": "d" * 40}, {"status": "failed"},
            {"errors": [{"phase": "sampling", "error_type": "TimeoutError"}]},
            {"errors": None}, {"dropped_events": {}}, {"dropped_events": {"queries": 1}},
            {"dropped_events": {"queries": False}}, {"coverage": None}, {"queries": []}, {"lanes": [{}]},
            {"operations": [{}]}, {"locks": None}, {"host": "unavailable"},
        ]
        for changed in changes:
            with self.subTest(changed=list(changed)):
                with self.assertRaises(ValueError):
                    self.verify({**self.record, **changed})
        for key, value in (("governance_operations", 1), ("query_events", 0), ("lock_samples", 0),
                           ("host_samples", 0), ("lane_starts", 4), ("operation_events", False)):
            with self.subTest(coverage=key):
                record = copy.deepcopy(self.record)
                record["coverage"][key] = value
                with self.assertRaises(ValueError):
                    self.verify(record)
        for malformed in (None, [], "unavailable"):
            self.output.write_text(json.dumps(malformed))
            with self.subTest(shape=type(malformed).__name__), self.assertRaises(ValueError):
                self.runner.verify_reference(observer=self.observer, identity=self.identity,
                    measurement=self.measurement, diagnostic=self.output, source_revision=REVISION)

    def test_changed_observer_or_measurement_identity_is_rejected(self):
        self.verify()
        for key, value in (("git_revision", "d" * 40), ("run_id", "not-an-identity"), ("workload_completed", None)):
            original = self.measurement.read_bytes()
            record = json.loads(original)
            record[key] = value
            self.measurement.write_text(json.dumps(record))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.runner.verify_reference(observer=self.observer, identity=self.identity,
                    measurement=self.measurement, diagnostic=self.output, source_revision=REVISION)
            self.measurement.write_bytes(original)
        (self.observer / self.runner.OBSERVER_FILES[0]).write_text("# A different observer\n")
        with self.assertRaisesRegex(ValueError, "shared observer"):
            self.runner.verify_reference(observer=self.observer, identity=self.identity,
                measurement=self.measurement, diagnostic=self.output, source_revision=REVISION)

    def test_retained_counters_cannot_hide_truncated_or_malformed_traces(self):
        for array, counter in (("operations", "operation_events"), ("queries", "query_events"),
                               ("locks", "lock_samples"), ("host", "host_samples"), ("lanes", "lane_starts")):
            for mutation in ("counter", "array", "shape"):
                record = copy.deepcopy(self.record)
                if mutation == "counter":
                    record["coverage"][counter] += 1
                elif mutation == "array":
                    record[array].append({})
                else:
                    record[array][0] = None
                with self.subTest(array=array, mutation=mutation), self.assertRaises(ValueError):
                    self.verify(record)

    def test_actual_governance_rows_and_all_five_unique_lanes_are_required(self):
        record = copy.deepcopy(self.record)
        record["operations"][0]["lane"] = "export"
        with self.assertRaisesRegex(ValueError, "Actual diagnostic governance"):
            self.verify(record)
        for changed in ("feed", "unexpected", None, []):
            record = copy.deepcopy(self.record)
            record["lanes"][-1]["lane"] = changed
            with self.subTest(lane=changed), self.assertRaisesRegex(ValueError, "paced lanes"):
                self.verify(record)
        record = copy.deepcopy(self.record)
        record["lanes"].append({"lane": "repair"})
        record["coverage"]["lane_starts"] += 1
        with self.assertRaisesRegex(ValueError, "paced lanes"):
            self.verify(record)

    def test_zero_lock_measurement_accepts_complete_empty_original_samples(self):
        measured = json.loads(self.measurement.read_text())
        measured["database"] = {"sampled_lock_waiting_query_age_peak_ms": 0,
                                "lock_wait_samples": 0, "waiting_sessions_peak": 0}
        self.measurement.write_text(json.dumps(measured))
        for sample in self.record["original_sampler_samples"]:
            sample["query_ages_ms"] = []
        self.record["sampler"].update(original_query_age_peak_ms=0, original_lock_wait_samples=0,
                                      original_waiting_sessions_peak=0)
        for graph in self.record["locks"]:
            graph.update(trigger="periodic", original_sample_peak_ms=0, connections=[])
        self.record["connection_alias_count"] = 0
        self.verify()

    def test_original_sampler_counts_and_peaks_must_reconcile_independently(self):
        for section, name, value in (
            ("coverage", "original_sampler_samples", 2), ("sampler", "observations", 2),
            ("sampler", "original_lock_wait_samples", 2), ("sampler", "original_waiting_sessions_peak", 1),
            ("sampler", "original_query_age_peak_ms", 3.456),
        ):
            record = copy.deepcopy(self.record)
            record[section][name] = value
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                self.verify(record)
        for section, name, value in (
            ("sampler", "samples", 2), ("sampler", "errors", ["RuntimeError"]),
            ("database", "lock_wait_samples", 2), ("database", "waiting_sessions_peak", 1),
            ("database", "sampled_lock_waiting_query_age_peak_ms", 3.456),
        ):
            original = self.measurement.read_bytes()
            measured = json.loads(original)
            measured[section][name] = value
            self.measurement.write_text(json.dumps(measured))
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                self.verify()
            self.measurement.write_bytes(original)

    def test_original_sampler_coverage_cannot_be_missing_or_truncated(self):
        for name in ("original_sampler_samples", "sampler"):
            record = copy.deepcopy(self.record)
            del record[name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                self.verify(record)
        for section, name in (("coverage", "original_sampler_samples"),
                              ("dropped_events", "original_sampler_samples")):
            record = copy.deepcopy(self.record)
            del record[section][name]
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.verify(record)
        for value in (None, [], "missing", self.record["original_sampler_samples"][:-1]):
            record = copy.deepcopy(self.record)
            record["original_sampler_samples"] = value
            with self.subTest(shape=type(value).__name__), self.assertRaises(ValueError):
                self.verify(record)
        record = copy.deepcopy(self.record)
        record["original_sampler_samples"][0] = None
        with self.assertRaises(ValueError):
            self.verify(record)

    def test_original_sampler_indices_times_and_numeric_ages_have_exact_types(self):
        for name, values in (
            ("index", (True, -1, 1, "0", None)),
            ("sample_ns", (True, -1, 1.0, "100", 2**63)),
            ("snapshot_elapsed_ns", (True, -1, 1.0, "1", 2**63)),
            ("query_ages_ms", (None, "0", [True], [-1], [float("nan")],
                               [float("inf")], ["2.5"], [10**400])),
        ):
            for value in values:
                record = copy.deepcopy(self.record)
                record["original_sampler_samples"][0][name] = value
                with self.subTest(name=name, value=repr(value)[:40]), self.assertRaises(ValueError):
                    self.verify(record)
        record = copy.deepcopy(self.record)
        record["original_sampler_samples"][1]["sample_ns"] = 99
        with self.assertRaises(ValueError):
            self.verify(record)

    def test_original_sampler_trace_has_bounded_rows_and_no_unrecognized_fields(self):
        record = copy.deepcopy(self.record)
        record["original_sampler_samples"] = [self.record["original_sampler_samples"][0]] * 40_001
        record["coverage"]["original_sampler_samples"] = 40_001
        with self.assertRaises(ValueError):
            self.verify(record)
        record = copy.deepcopy(self.record)
        record["original_sampler_samples"][0]["query_ages_ms"] = [0.0] * 65
        with self.assertRaises(ValueError):
            self.verify(record)
        for key in ("query", "parameters", "pid", "fixture_uuid", "private_path"):
            record = copy.deepcopy(self.record)
            record["original_sampler_samples"][0][key] = "private evidence"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(record)

    def test_original_sampler_summary_numbers_cannot_be_booleans_or_nonfinite(self):
        for name, values in (
            ("observations", (True, -1, 3.0)),
            ("original_lock_wait_samples", (True, -1, 3.0)),
            ("original_waiting_sessions_peak", (True, -1, 2.0)),
            ("original_query_age_peak_ms", (True, -1, float("nan"), float("inf"), 10**400)),
            ("gap_max_ns", (True, -1, 1.0, 2**63)),
            ("gap_mean_ns", (True, -1, 1.0, 2**63)),
        ):
            for value in values:
                record = copy.deepcopy(self.record)
                record["sampler"][name] = value
                with self.subTest(name=name, value=repr(value)[:40]), self.assertRaises(ValueError):
                    self.verify(record)
        for section, name, values in (
            ("sampler", "samples", (True, -1, 3.0)),
            ("database", "lock_wait_samples", (True, -1, 3.0)),
            ("database", "waiting_sessions_peak", (True, -1, 2.0)),
            ("database", "sampled_lock_waiting_query_age_peak_ms", (True, -1, float("nan"), float("inf"))),
        ):
            for value in values:
                original = self.measurement.read_bytes()
                measured = json.loads(original)
                measured[section][name] = value
                self.measurement.write_text(json.dumps(measured))
                with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                    self.verify()
                self.measurement.write_bytes(original)

    def test_lock_graph_provenance_and_new_maximum_context_are_required(self):
        for name, value in (("original_sample_index", True), ("original_sample_index", -1),
                            ("original_sample_index", 3), ("sample_ns", 201),
                            ("original_sample_peak_ms", 2.346), ("trigger", "other"),
                            ("trigger", "periodic")):
            record = copy.deepcopy(self.record)
            record["locks"][1][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.verify(record)
        for mutation in ("duplicate", "missing", "false_maximum"):
            record = copy.deepcopy(self.record)
            if mutation == "duplicate":
                record["locks"].append(copy.deepcopy(record["locks"][1]))
            elif mutation == "missing":
                record["locks"].pop(1)
            else:
                record["locks"][0]["trigger"] = "new_original_query_age_peak"
            record["coverage"]["lock_samples"] = len(record["locks"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.verify(record)

    def test_lock_graph_timestamps_and_numeric_values_are_bounded(self):
        for name in ("sample_ns", "observer_elapsed_ns", "observer_query_start_ns", "observer_query_end_ns"):
            for value in (True, -1, 1.0, "100", 2**63):
                record = copy.deepcopy(self.record)
                record["locks"][0][name] = value
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.verify(record)
        for name, value in (("observer_query_start_ns", 99), ("observer_query_end_ns", 101),
                            ("observer_elapsed_ns", 1), ("original_sample_peak_ms", True),
                            ("original_sample_peak_ms", float("nan"))):
            record = copy.deepcopy(self.record)
            record["locks"][0][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(record)

    def test_lock_connection_aliases_enums_and_ages_are_strict_and_private(self):
        for name, values in (
            ("connection_id", (True, 0, 2, "1")), ("category", ("SELECT secret", None)),
            ("wait_category", ("pid=123", None)), ("blocker_ids", (None, [True], [-1], [2], [0] * 17)),
            ("is_waiter", (1, None)), ("transaction_end_requested", (1, None)),
            ("query_age_ms", (True, -1, float("nan"), float("inf"))),
            ("transaction_age_ms", (True, -1, float("nan"))),
            ("current_lock_wait_age_ms", (True, -1, float("nan"), "1")),
            ("observed_fences", (None, ["policy_update"], ["policy_shared"] * 2)),
        ):
            for value in values:
                record = copy.deepcopy(self.record)
                record["locks"][1]["connections"][0][name] = value
                with self.subTest(name=name, value=repr(value)[:40]), self.assertRaises(ValueError):
                    self.verify(record)
        for key in ("query", "pid", "blocker_pids", "parameters", "fixture_uuid"):
            record = copy.deepcopy(self.record)
            record["locks"][1]["connections"][0][key] = "private evidence"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(record)
        self.record["locks"][1]["connections"][0]["current_lock_wait_age_ms"] = 0.75
        self.verify()

    def test_lock_graph_shapes_bounds_and_privacy_fields_fail_closed(self):
        for value in (None, "missing", [None], [{}] * 65):
            record = copy.deepcopy(self.record)
            record["locks"][0]["connections"] = value
            with self.subTest(shape=type(value).__name__), self.assertRaises(ValueError):
                self.verify(record)
        for key in ("sql", "pid", "original_query", "dsn"):
            record = copy.deepcopy(self.record)
            record["locks"][0][key] = "private evidence"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(record)
        for value in (True, -1, 65, "1"):
            record = copy.deepcopy(self.record)
            record["connection_alias_count"] = value
            with self.subTest(aliases=value), self.assertRaises(ValueError):
                self.verify(record)

    def test_workflow_selects_original_or_identical_observer_for_both_refs(self):
        source = (ROOT / ".github/workflows/capacity-trends.yml").read_text().splitlines()
        start = source.index("      - name: Measure both releases sequentially on this runner")
        start = source.index("        run: |", start) + 1
        body = []
        for line in source[start:]:
            if line and not line.startswith("          "):
                break
            body.append(line[10:] if line else "")
        command = "\n".join(body).replace(
            "/tmp/threatlens-capacity-python-${release}", str(self.directory / "python-${release}"),
        ).replace("/tmp/threatlens-capacity-${release}", str(self.directory / "reference-${release}"))
        binaries = self.directory / "bin"
        binaries.mkdir()
        events = self.directory / "events.jsonl"
        driver = binaries / "python"
        driver.write_text(f"#!{sys.executable}\n"
            "import json,os,sys\n"
            "with open(os.environ['DIAGNOSTIC_TEST_EVENTS'],'a') as output:\n"
            " output.write(json.dumps({'args':sys.argv[1:],'docker_host':os.environ.get('DOCKER_HOST'),'docker_context':os.environ.get('DOCKER_CONTEXT')})+'\\n')\n"
            "if '--print-docker-host' in sys.argv: print('unix:///run/owned-docker.sock')\n")
        driver.chmod(0o700)
        git = binaries / "git"
        git.write_text(f"#!{sys.executable}\nprint('{REVISION}')\n")
        git.chmod(0o700)
        for diagnostic in ("false", "true"):
            events.unlink(missing_ok=True)
            environment = dict(os.environ, GITHUB_WORKSPACE=str(self.directory),
                CAPACITY_PROFILE="sustained", CAPACITY_TARGET="fixture", CAPACITY_DIAGNOSTICS=diagnostic,
                DIAGNOSTIC_TEST_EVENTS=str(events), DOCKER_CONTEXT="ambient-context",
                PATH=str(binaries) + os.pathsep + os.environ.get("PATH", ""))
            result = subprocess.run(["bash", "-e", "-c", command], cwd=ROOT, env=environment,
                                    capture_output=True, text=True, timeout=5)
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = [json.loads(line) for line in events.read_text().splitlines()]
                workloads = [row for row in observed if row['args'][0].endswith('run_capacity_reference.py') and '--print-docker-host' not in row['args']]
                self.assertEqual(len(workloads), 2)
                for role, row in zip(("baseline", "candidate"), workloads, strict=True):
                    arguments = row['args'][row['args'].index('--') + 1:]
                    self.assertEqual(arguments[:3], ["timeout", "15m", str(self.directory / f"python-{role}/bin/python")])
                    if diagnostic == "false":
                        self.assertEqual(arguments[3], str(self.directory / f"reference-{role}/backend/scripts/run_capacity_baseline.py"))
                    else:
                        self.assertEqual(arguments[3], str(self.directory / 'scripts/operations/run_capacity_diagnostic.py'))
                        self.assertEqual(arguments[arguments.index('--entrypoint') + 1], str(self.directory / f"reference-{role}/backend/scripts/run_capacity_baseline.py"))
                        self.assertEqual(arguments[arguments.index('--observer-dir') + 1], str(self.directory / 'scripts/operations'))
                        self.assertEqual(arguments[arguments.index('--output') + 1], str(self.directory / f'capacity-results/{role}-diagnostics.json'))
                    self.assertEqual(arguments[-12:], ["--profile", "sustained", "--duration-seconds", "600", "--target-id", "fixture", "--cpu-count", "1", "--max-rss-mib", "1024", "--output", str(self.directory / f"capacity-results/{role}.json")])
                    self.assertEqual(row['docker_host'], 'unix:///run/owned-docker.sock')
                    self.assertIsNone(row['docker_context'])
                observers = [row for row in observed if row['args'][0].endswith('run_capacity_diagnostic.py')]
                self.assertEqual(len(observers), 3 if diagnostic == "true" else 0)
                if observers:
                    self.assertIn('--describe-observer', observers[0]['args'])
                    self.assertTrue(all('--verify' in row['args'] for row in observers[1:]))


if __name__ == "__main__":
    unittest.main()
