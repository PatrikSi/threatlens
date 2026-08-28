from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JOURNAL = REPOSITORY_ROOT / "scripts" / "recovery" / "recovery_journal.py"
FAILPOINT_ENV = "THREATLENS_RECOVERY_JOURNAL_TEST_FAILPOINT"


def _load_journal_helper():
    spec = importlib.util.spec_from_file_location(
        "test_recovery_journal_helper", JOURNAL
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery guard
        raise RuntimeError("Unable to load recovery journal helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecoveryJournalTests(unittest.TestCase):
    def _run(
        self, *arguments: str, failpoint: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        if failpoint is not None:
            environment[FAILPOINT_ENV] = failpoint
        return subprocess.run(
            [str(JOURNAL), *arguments],
            capture_output=True,
            text=True,
            env=environment,
        )

    def _init(
        self,
        root: Path,
        *,
        operation_id: str = "12345678-1234-4234-9234-123456789abc",
        failpoint: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            "init",
            "--root",
            str(root),
            "--operation-id",
            operation_id,
            "--started-at",
            "2026-08-27T12:00:00Z",
            "--project",
            "threatlens-test",
            "--database",
            "threatlens",
            "--archive-sha256",
            "a" * 64,
            "--target-config-sha256",
            "b" * 64,
            "--target-deployment-identity",
            "c" * 64,
            "--rollback-database",
            "tl_pre_restore_test",
            "--recovery-role",
            "tl_recovery_test",
            "--original-database-oid",
            "16384",
            "--original-role-can-login",
            "true",
            "--original-database-allow-connections",
            "true",
            failpoint=failpoint,
        )

    def _make_terminal(self, root: Path) -> None:
        result = self._run(
            "update",
            "--root",
            str(root),
            "--updated-at",
            "2026-08-27T12:06:00Z",
            "--phase",
            "completed",
            "--status",
            "failed",
            "--outcome",
            "rolled_back",
            "--error-code",
            "E_TEST",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_journal_survives_interruption_and_archives_only_when_terminal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "journal"
            created = self._init(root)
            self.assertEqual(created.returncode, 0, created.stderr)
            active = root / "active" / "journal.json"
            self.assertEqual(active.stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "active").stat().st_mode & 0o777, 0o700)

            duplicate = self._init(root)
            self.assertEqual(duplicate.returncode, 4)
            self.assertIn("unfinished recovery journal", duplicate.stderr)

            nonterminal_archive = self._run("archive", "--root", str(root))
            self.assertEqual(nonterminal_archive.returncode, 4)

            updated = self._run(
                "update",
                "--root",
                str(root),
                "--updated-at",
                "2026-08-27T12:05:00Z",
                "--phase",
                "clean_target_created",
                "--replacement-database-oid",
                "16385",
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            document = json.loads(active.read_text(encoding="utf-8"))
            self.assertEqual(document["replacement_database_oid"], "16385")

            terminal = self._run(
                "update",
                "--root",
                str(root),
                "--updated-at",
                "2026-08-27T12:06:00Z",
                "--phase",
                "completed",
                "--status",
                "failed",
                "--outcome",
                "rolled_back",
                "--error-code",
                "E_TEST",
            )
            self.assertEqual(terminal.returncode, 0, terminal.stderr)
            archived = self._run("archive", "--root", str(root))
            self.assertEqual(archived.returncode, 0, archived.stderr)
            history = root / "history" / "12345678-1234-4234-9234-123456789abc.json"
            self.assertTrue(history.is_file())
            self.assertFalse((root / "active").exists())

    def test_journal_rejects_symlinked_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir(mode=0o700)
            linked = base / "linked"
            linked.symlink_to(real, target_is_directory=True)

            result = self._init(linked)

        self.assertEqual(result.returncode, 4)
        self.assertIn("symlink", result.stderr)

    def test_prepare_creates_private_regular_operation_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "journal"

            result = self._run("prepare", "--root", str(root))

            self.assertEqual(result.returncode, 0, result.stderr)
            lock_path = root / "operation.lock"
            self.assertTrue(lock_path.is_file())
            self.assertFalse(lock_path.is_symlink())
            self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_nested_journal_root_fsyncs_each_new_parent_entry(self) -> None:
        helper = _load_journal_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal_root = root / "new-parent" / "journal"

            with mock.patch.object(helper, "_fsync_directory") as fsync_directory:
                helper._make_directories_without_links(journal_root)

            self.assertEqual(
                fsync_directory.call_args_list,
                [mock.call(root), mock.call(root / "new-parent")],
            )
            self.assertTrue(journal_root.is_dir())

    def test_init_recovers_death_before_atomic_active_publication(self) -> None:
        for failpoint in (
            "init_after_publication_directory",
            "init_after_journal_file_fsync",
            "init_after_journal_replace",
            "init_after_journal_fsync",
        ):
            with self.subTest(failpoint=failpoint), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "journal"
                interrupted = self._init(root, failpoint=failpoint)
                self.assertEqual(interrupted.returncode, 97, interrupted.stderr)
                self.assertFalse((root / "active").exists())

                retried = self._init(root)

                self.assertEqual(retried.returncode, 0, retried.stderr)
                self.assertTrue((root / "active" / "journal.json").is_file())
                self.assertFalse(
                    any(path.name.startswith(".active.init.") for path in root.iterdir())
                )

    def test_init_death_after_atomic_publish_leaves_complete_reconcilable_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "journal"
            interrupted = self._init(root, failpoint="init_after_active_rename")
            self.assertEqual(interrupted.returncode, 97, interrupted.stderr)

            inspected = self._run("inspect", "--root", str(root))
            duplicate = self._init(root)

            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertIn("prepared_before_fence", inspected.stdout)
            self.assertEqual(duplicate.returncode, 4)
            self.assertIn("unfinished recovery journal", duplicate.stderr)

    def test_archive_recovers_death_at_every_publication_and_cleanup_window(
        self,
    ) -> None:
        failpoints = (
            "archive_after_history_directory",
            "archive_after_history_directory_fsync",
            "archive_after_history_link",
            "archive_after_history_fsync",
            "archive_after_receipt_link",
            "archive_after_receipt_replace",
            "archive_after_receipt_fsync",
            "archive_after_active_rename",
            "archive_after_active_rename_fsync",
            "archive_after_journal_unlink",
            "archive_after_pending_fsync",
            "archive_after_pending_rmdir",
        )
        for index, failpoint in enumerate(failpoints, start=1):
            with self.subTest(failpoint=failpoint), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "journal"
                operation_id = f"12345678-1234-4234-9234-{index:012d}"
                created = self._init(root, operation_id=operation_id)
                self.assertEqual(created.returncode, 0, created.stderr)
                self._make_terminal(root)

                interrupted = self._run(
                    "archive", "--root", str(root), failpoint=failpoint
                )
                self.assertEqual(interrupted.returncode, 97, interrupted.stderr)

                inspected = self._run("inspect", "--root", str(root))
                self.assertEqual(inspected.returncode, 0, inspected.stderr)
                self.assertIn(operation_id, inspected.stdout)
                retried = self._run("archive", "--root", str(root))
                self.assertEqual(retried.returncode, 0, retried.stderr)

                history = root / "history" / f"{operation_id}.json"
                self.assertTrue(history.is_file())
                self.assertFalse((root / "active").exists())
                self.assertFalse(
                    any(
                        path.name.startswith(".active.archive.")
                        for path in root.iterdir()
                    )
                )
                self.assertFalse(
                    any(
                        path.name.startswith(".last-archived.json.")
                        for path in root.iterdir()
                    )
                )

                next_operation = f"87654321-4321-4321-8321-{index:012d}"
                restarted = self._init(root, operation_id=next_operation)
                self.assertEqual(restarted.returncode, 0, restarted.stderr)
                self.assertFalse((root / "last-archived.json").exists())

    def test_legacy_empty_active_directory_does_not_block_restore_and_archive_rerun(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "journal"
            prepared = self._run("prepare", "--root", str(root))
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            (root / "active").mkdir(mode=0o700)

            created = self._init(root)
            self.assertEqual(created.returncode, 0, created.stderr)
            self._make_terminal(root)
            archived = self._run("archive", "--root", str(root))
            self.assertEqual(archived.returncode, 0, archived.stderr)

            rerun = self._run("archive", "--root", str(root))
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertIn(".json", rerun.stdout)


if __name__ == "__main__":
    unittest.main()
