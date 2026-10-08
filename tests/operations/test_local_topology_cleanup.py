"""Simulated owned-resource cleanup and qualification evidence regressions."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

OPERATIONS = Path(__file__).resolve().parents[2] / "scripts/operations"
sys.path.insert(0, str(OPERATIONS))
import qualification_runtime  # noqa: E402
import qualify_local_topology  # noqa: E402


class LocalTopologyCleanupTests(unittest.TestCase):
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

    def run_qualification(self, *, workload_error=None, cleanup_error=None, diagnostic_error=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as patches:
            output = Path(directory) / "result.json"
            topology = MagicMock()
            topology.run_id = "a" * 32
            topology.image_ids = {"nginx": "sha256:" + "b" * 64}
            topology.cleanup_result = {"status": "failed" if cleanup_error else "passed"}
            topology.docker.return_value = "127.0.0.1:54321"
            topology.close.side_effect = cleanup_error

            def construct(temporary):
                if diagnostic_error:
                    (temporary / "api.log").write_text("synthetic diagnostic")
                return topology

            patches.enter_context(patch.object(sys, "argv", ["qualify_local_topology", "--output", str(output),
                "--duration-seconds", "10", "--target-id", "unit-test"]))
            patches.enter_context(patch.object(qualify_local_topology, "DisposableTopology", side_effect=construct))
            patches.enter_context(patch.object(qualify_local_topology.platform, "platform", return_value="synthetic Linux"))
            patches.enter_context(patch.object(qualify_local_topology.subprocess, "check_output", side_effect=["a" * 40, ""]))
            patches.enter_context(patch.object(qualify_local_topology.shutil, "copytree"))
            patches.enter_context(patch.object(qualify_local_topology.shutil, "copy2"))
            patches.enter_context(patch.object(qualify_local_topology, "free_port", side_effect=[17000, 17001]))
            patches.enter_context(patch.object(qualify_local_topology.httpx, "Client"))
            patches.enter_context(patch.object(qualify_local_topology, "wait_http"))
            patches.enter_context(patch.object(qualify_local_topology, "run_workload", side_effect=workload_error,
                return_value={"http_errors": [], "latency_p95_ms": 1}))
            patches.enter_context(patch.object(qualify_local_topology, "collect",
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
            return code, json.loads(output.read_text())

    def test_cleanup_error_cannot_leave_passed_evidence(self):
        code, result = self.run_qualification(cleanup_error=RuntimeError("cleanup failed"))
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["message"], "cleanup failed")

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
