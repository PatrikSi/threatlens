"""The reference runner must not hand an unclean baseline to its candidate."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "a" * 32
CONTAINER_ID = "b" * 64


def load_runner():
    operations = ROOT / "scripts/operations"
    with patch.object(sys, "path", [str(operations), *sys.path]):
        spec = importlib.util.spec_from_file_location(
            "capacity_reference_runner", operations / "run_capacity_reference.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def measurement_step(source):
    lines = source.splitlines()
    start = lines.index("      - name: Measure both releases sequentially on this runner")
    start = lines.index("        run: |", start) + 1
    body = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line else "")
    return "\n".join(body)


class CapacityReferenceWorkflowTests(unittest.TestCase):
    def test_owned_baseline_remnant_blocks_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            events = directory / "events.jsonl"
            binaries = directory / "bin"
            binaries.mkdir()
            (binaries / "python").symlink_to(sys.executable)
            docker = binaries / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['DOCKER_CAPACITY_TEST_EVENTS'], 'a') as out:\n"
                "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:3] == ['context', 'inspect']:\n"
                "    print('unix:///var/run/docker.sock')\n"
                "elif sys.argv[1:3] == ['ps', '-aq']:\n"
                f"    print('{CONTAINER_ID}')\n"
                "else:\n"
                "    sys.exit(97)\n"
            )
            docker.chmod(0o700)
            for role in ("baseline", "candidate"):
                interpreter = directory / f"python-{role}/bin/python"
                interpreter.parent.mkdir(parents=True)
                interpreter.symlink_to(sys.executable)
                harness = directory / f"reference-{role}/backend/scripts/run_capacity_baseline.py"
                harness.parent.mkdir(parents=True)
                harness.write_text(
                    "import json, os, pathlib, sys\n"
                    "with open(os.environ['DOCKER_CAPACITY_TEST_EVENTS'], 'a') as out:\n"
                    f"    out.write(json.dumps('{role}') + '\\n')\n"
                    "path = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
                    f"path.write_text(json.dumps({{'run_id': '{RUN_ID}', 'status': 'passed'}}))\n"
                )
            results = directory / "capacity-results"
            results.mkdir()
            source = (ROOT / ".github/workflows/capacity-trends.yml").read_text()
            command = measurement_step(source).replace(
                "/tmp/threatlens-capacity-python-${release}", str(directory / "python-${release}"),
            ).replace(
                "/tmp/threatlens-capacity-${release}", str(directory / "reference-${release}"),
            ).replace('> "capacity-results/${release}.log"', f'> "{results}/${{release}}.log"')
            environment = dict(os.environ, GITHUB_WORKSPACE=str(directory),
                               CAPACITY_PROFILE="sustained", CAPACITY_TARGET="fixture",
                               DOCKER_CAPACITY_TEST_EVENTS=str(events),
                               PATH=str(binaries) + os.pathsep + os.environ.get("PATH", ""))
            environment.pop("DOCKER_HOST", None)
            environment.pop("DOCKER_CONTEXT", None)
            result = subprocess.run(["bash", "-e", "-c", command], cwd=ROOT,
                                    env=environment, capture_output=True, text=True, timeout=10)
            self.assertTrue(events.exists(), result.stderr)
            observed = [json.loads(line) for line in events.read_text().splitlines()]
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual([row for row in observed if isinstance(row, str)], ["baseline"])
            probes = [row for row in observed if isinstance(row, list) and row[:2] == ["ps", "-aq"]]
            self.assertEqual(len(probes), 1)
            self.assertIn(f"label=threatlens.capacity.run_id={RUN_ID}", probes[0])
            self.assertIn("--no-trunc", probes[0])
            self.assertFalse(any(isinstance(row, list) and row[0] in {"rm", "kill"} for row in observed))


class CapacityReferenceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.artifact = self.directory / "measurement.json"
        self.artifact.write_text(json.dumps({"run_id": RUN_ID, "status": "passed"}))
        self.environment = {"DOCKER_HOST": "unix:///run/owned-docker.sock"}
        self.clock = 0.0

    def sleep(self, duration):
        self.clock += duration

    def attest(self, response):
        with patch.object(self.runner.subprocess, "run", side_effect=response) as run, patch.object(
            self.runner.time, "monotonic", side_effect=lambda: self.clock,
        ), patch.object(self.runner.time, "sleep", side_effect=self.sleep):
            result = self.runner.attest_cleanup(self.artifact, environment=self.environment)
        return result, run

    def test_zero_containers_is_verified_across_bounded_late_discovery(self):
        result, run = self.attest(lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=""))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["remaining_container_count"], 0)
        self.assertGreater(run.call_count, 1)
        self.assertLessEqual(self.clock, 3)
        for call in run.call_args_list:
            self.assertIs(call.kwargs["env"], self.environment)
            self.assertLessEqual(call.kwargs["timeout"], 2)
            self.assertEqual(call.args[0], ["docker", "ps", "-aq", "--no-trunc", "--filter",
                                           f"label=threatlens.capacity.run_id={RUN_ID}"])

    def test_owned_container_fails_without_any_removal(self):
        result, run = self.attest([subprocess.CompletedProcess([], 0, stdout=CONTAINER_ID + "\n")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["remaining_container_count"], 1)
        self.assertEqual(run.call_count, 1)

    def test_late_owned_container_fails_after_an_initial_empty_probe(self):
        result, run = self.attest([subprocess.CompletedProcess([], 0, stdout=""),
                                   subprocess.CompletedProcess([], 0, stdout=CONTAINER_ID)])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(run.call_count, 2)

    def test_unknown_docker_results_fail_closed(self):
        cases = [subprocess.CompletedProcess([], 17, stdout="", stderr="private response"),
                 subprocess.TimeoutExpired("docker", 2), OSError("private daemon detail"),
                 subprocess.CompletedProcess([], 0, stdout="truncated-id"),
                 subprocess.CompletedProcess([], 0, stdout=None)]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                result, run = self.attest([response])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(run.call_count, 1)
                self.assertNotIn("private", json.dumps(result))

    def test_invalid_or_unavailable_artifact_never_queries_docker(self):
        cases = [{}, {"run_id": None}, {"run_id": "a" * 31}, {"run_id": "A" * 32},
                 {"run_id": "x" * 32}, {"run_id": [RUN_ID]}, [], None]
        for artifact in cases:
            with self.subTest(artifact=artifact):
                self.artifact.write_text(json.dumps(artifact))
                result, run = self.attest([])
                self.assertEqual(result["status"], "failed")
                run.assert_not_called()
        self.artifact.unlink()
        result, run = self.attest([])
        self.assertEqual(result["status"], "failed")
        run.assert_not_called()

    def test_measurement_exit_is_preserved_when_cleanup_also_fails(self):
        for code in (3, 124):
            with self.subTest(code=code), patch.object(
                self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], code),
            ) as run, patch.object(self.runner, "attest_cleanup", return_value={"status": "failed"}):
                result = self.runner.run_reference(["timeout", "15m", "fixture"],
                    artifact=self.artifact, log=self.directory / "measurement.log",
                    attestation=self.directory / "cleanup.json", environment=self.environment)
            self.assertEqual(result, code)
            self.assertEqual(run.call_args.args[0], ["timeout", "15m", "fixture"])
            self.assertEqual(json.loads((self.directory / "cleanup.json").read_text())["status"], "failed")

    def test_successful_measurement_is_rejected_by_failed_cleanup(self):
        with patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(
            self.runner, "attest_cleanup", return_value={"status": "failed"},
        ):
            result = self.runner.run_reference(["timeout", "15m", "fixture"],
                artifact=self.artifact, log=self.directory / "measurement.log",
                attestation=self.directory / "cleanup.json", environment=self.environment)
        self.assertEqual(result, 1)

    def test_verified_cleanup_allows_both_refs_on_the_same_frozen_endpoint(self):
        artifacts = [self.artifact, self.directory / "candidate.json"]
        artifacts[1].write_text(json.dumps({"run_id": "c" * 32, "status": "passed"}))

        def run(command, **kwargs):
            self.assertIs(kwargs["env"], self.environment)
            self.assertNotIn("DOCKER_CONTEXT", kwargs["env"])
            return subprocess.CompletedProcess(command, 0, stdout="")

        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote.example:2375", "DOCKER_CONTEXT": "remote"}, clear=True), patch.object(
            self.runner.subprocess, "run", side_effect=run,
        ) as commands, patch.object(self.runner.time, "monotonic", side_effect=lambda: self.clock), patch.object(
            self.runner.time, "sleep", side_effect=self.sleep,
        ):
            for index, artifact in enumerate(artifacts):
                original_measurement = artifact.read_bytes()
                result = self.runner.run_reference(["timeout", "15m", f"reference-{index}"],
                    artifact=artifact, log=self.directory / f"reference-{index}.log",
                    attestation=self.directory / f"cleanup-{index}.json", environment=self.environment)
                self.assertEqual(result, 0)
                self.assertEqual(artifact.read_bytes(), original_measurement)
                evidence = json.loads((self.directory / f"cleanup-{index}.json").read_text())
                self.assertEqual(evidence["status"], "passed")
        measured = [call.args[0] for call in commands.call_args_list if call.args[0][0] == "timeout"]
        self.assertEqual(measured, [["timeout", "15m", "reference-0"], ["timeout", "15m", "reference-1"]])
        labels = {call.args[0][-1] for call in commands.call_args_list if call.args[0][0] == "docker"}
        self.assertEqual(labels, {f"label=threatlens.capacity.run_id={RUN_ID}", "label=threatlens.capacity.run_id=" + "c" * 32})

    def test_unfrozen_or_remote_endpoint_never_queries_docker(self):
        for environment in [{"DOCKER_HOST": "tcp://remote.example:2375"},
                            {"DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": "remote"}, {}]:
            with self.subTest(environment=environment), patch.object(self.runner.subprocess, "run") as run:
                result = self.runner.attest_cleanup(self.artifact, environment=environment)
                self.assertEqual(result["status"], "failed")
                run.assert_not_called()

    def test_remote_context_is_refused_before_measurement(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": "remote"}, clear=True), patch.object(
            self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="ssh://remote.example"),
        ) as run, self.assertRaisesRegex(ValueError, "local Docker Unix socket"):
            self.runner.require_local_docker()
        self.assertEqual(run.call_args.args[0][:4], ["docker", "context", "inspect", "remote"])


if __name__ == "__main__":
    unittest.main()
