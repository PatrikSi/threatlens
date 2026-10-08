"""Owned vendor setup preserves real failures and always attempts scoped cleanup."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/operations"))
import run_opensearch_qualification as qualification  # noqa: E402

CID = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
RUN_ID = "c" * 32


class Clock:
    elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class Docker:
    def __init__(self, *, logs_fail=False, contract_fail=False, create_timeout=False,
                 cleanup_fail=False, wrong_label=False, readiness_timeouts=0, host="unix:///var/run/docker.sock"):
        self.logs_fail, self.contract_fail = logs_fail, contract_fail
        self.create_timeout, self.cleanup_fail = create_timeout, cleanup_fail
        self.wrong_label, self.readiness_timeouts, self.host = wrong_label, readiness_timeouts, host
        self.created = self.removed = False
        self.calls = []

    def __call__(self, arguments, *, timeout=15):
        self.calls.append((arguments, timeout))
        prefix = arguments[:2]
        output = ""
        if prefix == ["git", "rev-parse"]:
            output = "d" * 40
        elif prefix == ["docker", "context"]:
            output = self.host
        elif arguments[:3] == ["docker", "image", "inspect"]:
            output = IMAGE_ID
        elif prefix == ["docker", "run"]:
            self.created = True
            if self.create_timeout:
                raise subprocess.TimeoutExpired(arguments, timeout)
            output = CID
        elif prefix == ["docker", "port"]:
            output = "127.0.0.1:49123"
        elif prefix == ["docker", "exec"]:
            if self.readiness_timeouts:
                self.readiness_timeouts -= 1
                raise subprocess.TimeoutExpired(arguments, timeout)
        elif len(arguments) > 1 and Path(arguments[1]).name == "qualify_opensearch.py":
            if self.contract_fail:
                raise subprocess.CalledProcessError(1, arguments)
            destination = Path(arguments[arguments.index("--output") + 1])
            destination.write_text(json.dumps({"opensearch_version": "3.8.0", "external_launches": 2}))
        elif prefix == ["docker", "logs"] and self.logs_fail:
            if isinstance(self.logs_fail, Exception):
                raise self.logs_fail
            raise subprocess.TimeoutExpired(arguments, timeout)
        elif prefix == ["docker", "ps"]:
            output = CID if self.created and not self.removed else ""
        elif prefix == ["docker", "inspect"]:
            output = "someone-else" if self.wrong_label else RUN_ID
        elif prefix == ["docker", "rm"]:
            if self.cleanup_fail:
                raise subprocess.TimeoutExpired(arguments, timeout)
            self.removed = True
        return SimpleNamespace(stdout=output, stderr="", returncode=0)


class OpenSearchQualificationRunnerTests(unittest.TestCase):
    def invoke(self, docker, *, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "qualification.json"
            clock = Clock()
            with patch.object(qualification, "command", docker), \
                    patch.object(qualification.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(qualification.uuid, "uuid4", return_value=SimpleNamespace(hex=RUN_ID)), \
                    patch.object(qualification.time, "monotonic", clock.monotonic), \
                    patch.object(qualification.time, "sleep", clock.sleep), \
                    patch.object(qualification, "READINESS_SECONDS", 2), \
                    patch.dict(os.environ, environment or {}, clear=True):
                result = qualification.run_qualification(output)
            self.assertEqual(json.loads(output.read_text()), result)
            return result

    def test_success_uses_pinned_caps_loopback_and_exact_ownership(self):
        docker = Docker()
        result = self.invoke(docker)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["source_revision"], "d" * 40)
        self.assertEqual(result["vendor_image_id"], IMAGE_ID)
        self.assertFalse(result["production_qualified"])
        self.assertTrue(result["cleanup"]["completed"])
        creation = next(command for command, _ in docker.calls if command[:2] == ["docker", "run"])
        for option, value in (("--pull", "never"), ("--publish", "127.0.0.1::9200"),
                              ("--memory", "2g"), ("--memory-swap", "2g"), ("--cpus", "2"),
                              ("--pids-limit", "512"), ("--label", f"{qualification.LABEL}={RUN_ID}")):
            self.assertEqual(creation[creation.index(option) + 1], value)
        self.assertEqual(creation[-1], IMAGE_ID)
        self.assertTrue(docker.removed)

    def test_diagnostic_timeout_does_not_skip_cleanup_or_success_evidence(self):
        docker = Docker(logs_fail=True)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["diagnostic_error"], "TimeoutExpired")
        self.assertTrue(docker.removed)

    def test_contract_failure_survives_diagnostic_failure_and_cleanup(self):
        docker = Docker(logs_fail=True, contract_fail=True)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"], {"stage": "contract", "category": "CalledProcessError"})
        self.assertEqual(result["contract_status"], "failed")
        self.assertTrue(docker.removed)

    def test_unexpected_diagnostic_error_still_attempts_cleanup_and_writes_evidence(self):
        docker = Docker(logs_fail=ValueError("invalid diagnostic text"))
        result = self.invoke(docker)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["diagnostic_error"], "ValueError")
        self.assertTrue(docker.removed)

    def test_unexpected_cleanup_error_is_recorded_as_failed_evidence(self):
        with patch.object(qualification, "cleanup_owned", side_effect=RuntimeError("cleanup unavailable")):
            result = self.invoke(Docker())
        self.assertEqual(result["contract_status"], "passed")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["cleanup"], {"completed": False, "errors": ["cleanup_failed:RuntimeError"]})

    def test_cleanup_failure_cannot_turn_a_successful_contract_green(self):
        result = self.invoke(Docker(cleanup_fail=True))
        self.assertEqual(result["contract_status"], "passed")
        self.assertEqual(result["status"], "failed")
        self.assertIn("owned_container_removal_failed", result["cleanup"]["errors"])

    def test_accepted_creation_with_lost_response_is_discovered_by_its_exact_label(self):
        docker = Docker(create_timeout=True)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["stage"], "container_start")
        self.assertTrue(docker.removed)
        self.assertTrue(result["cleanup"]["completed"])
        self.assertEqual(sum(command[:2] == ["docker", "run"] for command, _ in docker.calls), 1)

    def test_ownership_mismatch_never_removes_a_container(self):
        docker = Docker(wrong_label=True)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ownership_label_mismatch", result["cleanup"]["errors"])
        self.assertFalse(docker.removed)

    def test_transient_readiness_timeout_retries_only_the_read_only_probe(self):
        docker = Docker(readiness_timeouts=1)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(sum(command[:2] == ["docker", "exec"] for command, _ in docker.calls), 2)
        self.assertEqual(sum(command[:2] == ["docker", "run"] for command, _ in docker.calls), 1)

    def test_readiness_deadline_produces_failure_and_cleanup(self):
        docker = Docker(readiness_timeouts=100)
        result = self.invoke(docker)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"], {"stage": "readiness", "category": "TimeoutError"})
        self.assertTrue(docker.removed)

    def test_remote_context_overrides_host_and_is_refused_before_creation(self):
        docker = Docker(host="ssh://somewhere")
        result = self.invoke(docker, environment={"DOCKER_HOST": "unix:///local.sock", "DOCKER_CONTEXT": "remote"})
        self.assertEqual(result["status"], "failed")
        self.assertFalse(docker.created)

    def test_application_credentials_and_inherited_python_path_are_not_forwarded(self):
        with patch.dict(os.environ, {"DATABASE_URL": "private", "OPENSEARCH_AUTHORIZATION": "private",
                                     "PYTHONPATH": "/private", "PATH": "/usr/bin"}, clear=True):
            self.assertEqual(qualification.clean_environment(), {"PATH": "/usr/bin", "PYTHONDONTWRITEBYTECODE": "1"})


if __name__ == "__main__":
    unittest.main()
