"""Image and proxy gates must reject leaks and retain the original failure."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
        spec.loader.exec_module(module)
    return module


artifacts = load("verify_image_dependency_artifacts")
proxy = load("verify_mcp_proxy")
isolation = load("verify_runtime_isolation")


class ImageProxyCleanupTests(unittest.TestCase):
    def test_remote_mcp_daemon_is_refused_before_image_or_network_operations(self):
        with patch.dict("os.environ", {"DOCKER_HOST": "ssh://remote.example"}, clear=True), patch.object(
            proxy.subprocess, "run",
        ) as run, self.assertRaisesRegex(ValueError, "local Docker Unix socket"):
            proxy.verify(None)
        run.assert_not_called()

    def test_remote_runtime_daemon_is_refused_before_fixture_creation(self):
        with patch.object(sys, "argv", ["verify_runtime_isolation", "--output", "/tmp/unused-runtime-qualification.json"]), patch.dict(
            "os.environ", {"DOCKER_HOST": "tcp://remote.example:2376"}, clear=True,
        ), patch.object(isolation.tempfile, "mkdtemp") as create, patch.object(
            isolation.subprocess, "run",
        ) as run, self.assertRaisesRegex(ValueError, "local Docker Unix socket"):
            isolation.main()
        create.assert_not_called()
        run.assert_not_called()

    def test_proxy_docker_and_cleanup_stay_on_the_validated_socket_and_sdk_environment_is_unchanged(self):
        selected = {"DOCKER_HOST": "unix:///var/run/docker.sock"}

        def exercise(_args):
            with patch.dict("os.environ", {"DOCKER_CONTEXT": "remote"}, clear=True):
                proxy.run("docker", "ps", "--quiet")
                proxy.cleanup_owned_resources(["owned-container"], ["owned-network"])
                proxy.run("sdk-python", "--version")

        with patch.object(proxy, "require_local_docker", return_value=selected), patch.object(
            proxy, "_verify", side_effect=exercise,
        ), patch.object(proxy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="")) as run:
            proxy.verify(None)
        self.assertTrue(all(call.kwargs["env"] == selected for call in run.call_args_list[:-1]))
        self.assertIsNone(run.call_args_list[-1].kwargs["env"])
        self.assertIsNone(proxy.DOCKER_ENVIRONMENT.get())

    def test_failed_proxy_verification_does_not_retain_a_daemon_binding(self):
        original = ValueError("protocol failed")
        with patch.object(proxy, "require_local_docker", return_value={"DOCKER_HOST": "unix:///var/run/docker.sock"}), patch.object(
            proxy, "_verify", side_effect=original,
        ), self.assertRaises(ValueError) as caught:
            proxy.verify(None)
        self.assertIs(caught.exception, original)
        self.assertIsNone(proxy.DOCKER_ENVIRONMENT.get())

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

    def assert_ambiguous_network_cleanup(self, failed_creation):
        requested = []

        def command(*arguments, **kwargs):
            if arguments[:3] == ("docker", "network", "create"):
                requested.append(arguments[-1])
                if len(requested) == failed_creation:
                    raise subprocess.TimeoutExpired(arguments, 60)
            return "sha256:fixture"

        args = SimpleNamespace(backend_image="fixture-backend", web_image="fixture-web",
                               postgres_image="fixture-db", redis_image="fixture-redis", sdk_python=None)
        with patch.object(proxy, "require_local_docker", return_value={"DOCKER_HOST": "unix:///var/run/docker.sock"}), patch.object(
            proxy, "run", side_effect=command,
        ), patch.object(
            proxy, "cleanup_owned_resources"
        ) as cleanup:
            with self.assertRaises(subprocess.TimeoutExpired):
                proxy.verify(args)
        self.assertEqual(cleanup.call_args.args[1], requested)

    def test_ambiguous_internal_network_creation_is_in_cleanup_scope(self):
        self.assert_ambiguous_network_cleanup(1)

    def test_ambiguous_ingress_network_creation_is_in_cleanup_scope(self):
        self.assert_ambiguous_network_cleanup(2)


if __name__ == "__main__":
    unittest.main()
