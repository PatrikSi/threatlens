"""Simulated owned-resource cleanup and qualification evidence regressions."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

OPERATIONS = Path(__file__).resolve().parents[2] / "scripts/operations"
sys.path.insert(0, str(OPERATIONS))
import qualification_runtime  # noqa: E402

# Recovery CI intentionally installs only cryptography. This test never makes
# HTTP calls, so confine a synthetic client module to the CLI's mocked import.
client_module = ModuleType("httpx")
client_module.Client = MagicMock()
with patch.dict(sys.modules, {"httpx": client_module}):
    import qualify_local_topology


class LocalTopologyCleanupTests(unittest.TestCase):
    def test_remote_host_is_refused_before_any_docker_operation(self):
        with patch.dict("os.environ", {"DOCKER_HOST": "tcp://remote.example:2376"}, clear=True), patch(
            "qualification_runtime.subprocess.run",
        ) as run, self.assertRaisesRegex(ValueError, "local Docker Unix socket"):
            qualification_runtime.require_local_docker()
        run.assert_not_called()

    def test_remote_context_overrides_an_explicit_local_host_and_is_refused(self):
        with patch.dict("os.environ", {"DOCKER_CONTEXT": "remote", "DOCKER_HOST": "unix:///var/run/docker.sock"}, clear=True), patch(
            "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="ssh://remote.example\n"),
        ) as run, self.assertRaisesRegex(ValueError, "local Docker Unix socket"):
            qualification_runtime.require_local_docker()
        self.assertEqual(run.call_args.args[0][:4], ["docker", "context", "inspect", "remote"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_unknown_context_is_refused_and_local_socket_is_accepted(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout=""),
        ), self.assertRaisesRegex(RuntimeError, "Docker endpoint"):
            qualification_runtime.require_local_docker()
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///var/run/docker.sock"}, clear=True), patch(
            "qualification_runtime.subprocess.run",
        ) as run:
            qualification_runtime.require_local_docker()
        run.assert_not_called()

    def test_validated_socket_stays_bound_after_the_callers_context_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DOCKER_CONTEXT": "local"}, clear=True), patch(
            "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="unix:///var/run/docker.sock\n"),
        ) as run:
            environment = qualification_runtime.require_local_docker()
            self.assertNotIn("DOCKER_CONTEXT", environment)
            topology = qualification_runtime.DisposableTopology(Path(directory), docker_environment=environment)
            environment["DOCKER_HOST"] = "tcp://remote.example:2376"
            with patch.dict("os.environ", {"DOCKER_CONTEXT": "remote"}):
                topology.docker("ps", "--quiet")
            self.assertEqual(run.call_args.kwargs["env"]["DOCKER_HOST"], "unix:///var/run/docker.sock")
            self.assertNotIn("DOCKER_CONTEXT", run.call_args.kwargs["env"])

    def test_nonzero_removal_fails_after_attempting_every_container(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            topology.containers = ["owned-first", "owned-second"]
            with patch.object(topology, "docker", side_effect=["a" * 12 + "\n" + "b" * 12, ""]), patch(
                "qualification_runtime.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0),
                ],
            ) as remove, self.assertRaisesRegex(RuntimeError, "cleanup"):
                topology.close()
            self.assertEqual(remove.call_count, 2)

    def test_ambiguous_create_is_discovered_and_removed_with_anonymous_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", side_effect=["sha256:" + "b" * 64,
                subprocess.TimeoutExpired("docker", 60), "a" * 12, ""]) as discover, patch(
                "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ) as remove:
                with self.assertRaises(subprocess.TimeoutExpired):
                    topology.service("postgres", "postgres:16")
                topology.close()
            self.assertEqual(discover.call_count, 4)
            self.assertIn(f"label=threatlens.qualification.run={topology.run_id}", discover.call_args.args)
            self.assertIn("--volumes", remove.call_args.args[0])
            self.assertEqual(remove.call_args.args[0][-1], "a" * 12)

    def test_late_owned_create_gets_one_additional_removal_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", side_effect=["", "a" * 12, ""]) as discover, patch(
                "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ) as remove:
                topology.close()
            self.assertEqual(discover.call_count, 3)
            self.assertEqual(remove.call_count, 1)
            self.assertEqual(topology.cleanup_result["remaining_container_ids"], [])

    def test_remaining_owned_container_fails_after_bounded_sweeps(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", return_value="a" * 12), patch(
                "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ) as remove, self.assertRaisesRegex(RuntimeError, "cleanup"):
                topology.close()
            self.assertEqual(remove.call_count, 2)
            self.assertEqual(topology.cleanup_result["remaining_container_ids"], ["a" * 12])

    def test_unavailable_cleanup_inventory_is_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", side_effect=RuntimeError("inventory unavailable")), self.assertRaisesRegex(
                RuntimeError, "cleanup",
            ):
                topology.close()

    def test_initial_inventory_failure_still_removes_later_discovered_owned_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", side_effect=[RuntimeError("inventory unavailable"), "a" * 12, ""]), patch(
                "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ) as remove, self.assertRaisesRegex(RuntimeError, "cleanup"):
                topology.close()
            self.assertEqual(remove.call_count, 1)
            self.assertEqual(topology.cleanup_result["remaining_container_ids"], [])
            self.assertIn("container-discovery", topology.cleanup_result["errors"])

    def test_final_inventory_failure_records_unknown_instead_of_stale_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = qualification_runtime.DisposableTopology(Path(directory))
            with patch.object(topology, "docker", side_effect=["a" * 12, "a" * 12, RuntimeError("inventory unavailable")]), patch(
                "qualification_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ), self.assertRaisesRegex(RuntimeError, "cleanup"):
                topology.close()
            self.assertIsNone(topology.cleanup_result["remaining_container_ids"])
            self.assertIn("container-verification", topology.cleanup_result["errors"])

    def run_qualification(self, *, workload_error=None, cleanup_error=None, diagnostic_error=False, docker_guard_error=None,
                          monitor_calls=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as patches:
            output = Path(directory) / "result.json"
            topology = MagicMock()
            topology.run_id = "a" * 32
            topology.image_ids = {"nginx": "sha256:" + "b" * 64}
            topology.cleanup_result = {"status": "failed" if cleanup_error else "passed"}
            topology.docker.return_value = "127.0.0.1:54321"
            topology.close.side_effect = cleanup_error

            def construct(temporary, *, docker_environment):
                if diagnostic_error:
                    (temporary / "api.log").write_text("synthetic diagnostic")
                return topology

            patches.enter_context(patch.object(sys, "argv", ["qualify_local_topology", "--output", str(output),
                "--duration-seconds", "10", "--target-id", "unit-test"]))
            patches.enter_context(patch.object(qualify_local_topology, "DisposableTopology", side_effect=construct))
            patches.enter_context(patch.object(qualify_local_topology, "require_local_docker", side_effect=docker_guard_error,
                return_value={"DOCKER_HOST": "unix:///var/run/docker.sock"}))
            patches.enter_context(patch.object(qualify_local_topology.platform, "platform", return_value="synthetic Linux"))
            patches.enter_context(patch.object(qualify_local_topology.subprocess, "check_output", side_effect=["a" * 40, ""]))
            patches.enter_context(patch.object(qualify_local_topology.shutil, "copytree"))
            patches.enter_context(patch.object(qualify_local_topology.shutil, "copy2"))
            patches.enter_context(patch.object(qualify_local_topology, "free_port", side_effect=[17000, 17001]))
            patches.enter_context(patch.object(qualify_local_topology.httpx, "Client"))
            patches.enter_context(patch.object(qualify_local_topology, "wait_http"))
            patches.enter_context(patch.object(qualify_local_topology, "run_workload", side_effect=workload_error,
                return_value={"http_errors": [], "latency_p95_ms": 1}))
            monitor = patches.enter_context(patch.object(qualify_local_topology, "collect",
                return_value=({"fleet_available": True, "active_incidents": []}, None)))
            patches.enter_context(patch.dict("os.environ", {}, clear=True))
            patches.enter_context(patch("builtins.print"))
            if diagnostic_error:
                read_bytes = Path.read_bytes

                def read_diagnostic(path):
                    if path.name == "api.log":
                        raise OSError("diagnostic unavailable")
                    return read_bytes(path)

                patches.enter_context(patch.object(Path, "read_bytes", autospec=True, side_effect=read_diagnostic))
            code = qualify_local_topology.main()
            if monitor_calls is not None:
                monitor_calls.extend(monitor.call_args_list)
            return code, json.loads(output.read_text())

    def test_independent_monitor_uses_the_qualification_socket_after_context_changes(self):
        calls = []
        code, _result = self.run_qualification(monitor_calls=calls)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("fleet", calls[0].kwargs)
        observer = calls[0].kwargs["fleet"]
        with patch.dict("os.environ", {"DOCKER_CONTEXT": "remote"}, clear=True), patch(
            "fleet.subprocess.Popen", side_effect=OSError("mock observation unavailable"),
        ) as observe, self.assertRaises(OSError):
            observer(calls[0].args[0])
        self.assertEqual(observe.call_args.kwargs["env"], {"DOCKER_HOST": "unix:///var/run/docker.sock"})
        self.assertNotIn("DOCKER_CONTEXT", observe.call_args.kwargs["env"])

    def test_cleanup_error_cannot_leave_passed_evidence(self):
        code, result = self.run_qualification(cleanup_error=RuntimeError("cleanup failed"))
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["message"], "cleanup failed")

    def test_daemon_boundary_failure_is_recorded_before_fixture_creation(self):
        code, result = self.run_qualification(docker_guard_error=ValueError("local daemon required"))
        self.assertEqual(code, 1)
        self.assertEqual(result["failure"], {"type": "ValueError", "message": "local daemon required"})
        self.assertNotIn("run_id", result)

    def test_cleanup_preserves_the_original_workload_failure(self):
        code, result = self.run_qualification(workload_error=ValueError("workload failed"),
            cleanup_error=RuntimeError("cleanup failed"))
        self.assertEqual(code, 1)
        self.assertEqual(result["failure"], {"type": "ValueError", "message": "workload failed"})
        self.assertEqual(result["cleanup_failure"], {"type": "RuntimeError", "message": "cleanup failed"})

    def test_diagnostic_error_does_not_replace_the_workload_failure(self):
        code, result = self.run_qualification(workload_error=ValueError("workload failed"), diagnostic_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(result["failure"], {"type": "ValueError", "message": "workload failed"})
        self.assertEqual(result["diagnostic_errors"], [{"type": "OSError"}])

    def test_success_records_verified_cleanup(self):
        code, result = self.run_qualification()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result.get("cleanup"), {"status": "passed"})


if __name__ == "__main__":
    unittest.main()
