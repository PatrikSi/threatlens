"""Cleanup failures must fail qualification without erasing the restore failure."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.operations import test_host_loss_drill as host_loss  # noqa: E402
from tests.recovery import test_recovery_docker_e2e as recovery  # noqa: E402


class RecoveryCleanupEvidenceTests(unittest.TestCase):
    def fixture(self):
        fixture = recovery.RecoveryDockerEndToEndTests("test_backup_drill_and_destructive_restore_preserve_invariants")
        fixture.temporary_directory = Mock()
        fixture._compose = Mock(return_value=SimpleNamespace(returncode=0))
        return fixture

    def test_nonzero_compose_cleanup_fails_and_still_cleans_temporary_directory(self):
        fixture = self.fixture()
        fixture._compose.return_value.returncode = 1
        with self.assertRaisesRegex(AssertionError, "cleanup failed"):
            fixture.tearDown()
        fixture.temporary_directory.cleanup.assert_called_once_with()

    def test_compose_cleanup_timeout_still_cleans_temporary_directory(self):
        fixture = self.fixture()
        fixture._compose.side_effect = TimeoutError("owned cleanup timed out")
        with self.assertRaises(TimeoutError):
            fixture.tearDown()
        fixture.temporary_directory.cleanup.assert_called_once_with()

    def test_successful_restore_with_failed_cleanup_writes_failed_evidence(self):
        helper = SimpleNamespace(tearDown=Mock(side_effect=RuntimeError("cleanup failed")))
        result = {"status": "passed", "restore_verified": True}
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, "COMPOSE_FILE", Path("changed")):
            output = Path(directory) / "result.json"
            with self.assertRaises(RuntimeError):
                host_loss.finish_qualification(helper, result, output, Path("original"), None)
            self.assertEqual(recovery.COMPOSE_FILE, Path("original"))
            self.assertEqual(json.loads(output.read_text())["status"], "failed")
            self.assertTrue(result["restore_verified"])
            self.assertEqual(result["cleanup"], {"completed": False, "category": "RuntimeError"})

    def test_restore_failure_is_preserved_when_cleanup_also_fails(self):
        original = ValueError("restore failed")
        helper = SimpleNamespace(tearDown=Mock(side_effect=RuntimeError("cleanup failed")))
        result = {"status": "failed", "failure": {"category": "ValueError"}}
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, "COMPOSE_FILE", Path("changed")):
            output = Path(directory) / "result.json"
            with self.assertRaises(ValueError) as caught:
                try:
                    raise original
                finally:
                    host_loss.finish_qualification(helper, result, output, Path("original"), sys.exc_info()[1])
            self.assertIs(caught.exception, original)
            self.assertEqual(json.loads(output.read_text())["failure"], {"category": "ValueError"})
            self.assertFalse(result["cleanup"]["completed"])

    def test_successful_cleanup_preserves_passed_result(self):
        helper = SimpleNamespace(tearDown=Mock())
        result = {"status": "passed"}
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery, "COMPOSE_FILE", Path("changed")):
            output = Path(directory) / "result.json"
            host_loss.finish_qualification(helper, result, output, Path("original"), None)
            self.assertEqual(json.loads(output.read_text())["status"], "passed")
            self.assertTrue(result["cleanup"]["completed"])


if __name__ == "__main__":
    unittest.main()
