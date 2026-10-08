"""Image and proxy gates must reject leaks and retain the original failure."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifacts = load("verify_image_dependency_artifacts")
proxy = load("verify_mcp_proxy")


class ImageProxyCleanupTests(unittest.TestCase):
    def test_image_cleanup_nonzero_rejects_successful_artifact_check(self):
        def command(arguments, **kwargs):
            if arguments[:2] == ["docker", "rm"]:
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, arguments)
                return subprocess.CompletedProcess(arguments, 1)
            return subprocess.CompletedProcess(arguments, 0)

        with patch.object(artifacts.subprocess, "run", side_effect=command), patch.object(
            artifacts, "file_hashes", return_value={"fixture": "same"}
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                artifacts.verify_image("backend", "sha256:fixture", Path("reference"))

    def test_image_failure_survives_cleanup_timeout(self):
        original = ValueError("inventory unavailable")
        with patch.object(artifacts.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
            subprocess.TimeoutExpired("docker rm", 30),
        ]), patch.object(artifacts, "file_hashes", side_effect=original):
            with self.assertRaises(ValueError) as caught:
                artifacts.verify_image("backend", "sha256:fixture", Path("reference"))
        self.assertIs(caught.exception, original)
        self.assertIn("cleanup failed", original.__notes__[0])

    def test_proxy_nonzero_cleanup_rejects_pass_and_attempts_remaining_resources(self):
        def command(arguments, **kwargs):
            if arguments[-1] == "api":
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, arguments)
                return subprocess.CompletedProcess(arguments, 1)
            return subprocess.CompletedProcess(arguments, 0)

        with patch.object(proxy.subprocess, "run", side_effect=command) as run:
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                proxy.cleanup_owned_resources(["db", "api"], ["fixture-network"])
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         ["api", "db", "fixture-network"])

    def test_proxy_cleanup_timeout_does_not_skip_network_or_replace_failure(self):
        original = ValueError("protocol failed")
        with patch.object(proxy.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("docker rm", 20),
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
        ]) as run:
            proxy.cleanup_owned_resources(["db", "api"], ["fixture-network"], original)
        self.assertEqual(run.call_count, 3)
        self.assertIn("api: TimeoutExpired", original.__notes__[0])

    def test_successful_cleanup_checks_all_exit_statuses(self):
        with patch.object(proxy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            proxy.cleanup_owned_resources(["api"], ["fixture-network"])
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
