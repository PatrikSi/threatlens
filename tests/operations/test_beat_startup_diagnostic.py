"""The opt-in startup launcher must preserve the original Celery contract."""
from __future__ import annotations

import ast
import builtins
import importlib.util
import io
import os
from pathlib import Path
import signal
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/operations/diagnose_beat_startup.py"
ARGUMENTS = [
    "/opt/threatlens-beat-diagnostic/bin/celery",
    "-A", "app.tasks.celery_app.celery_app", "beat", "--loglevel=INFO",
    "--scheduler=app.tasks.beat_scheduler:WatchdogPersistentScheduler",
    "--schedule=/tmp/threatlens-celerybeat-schedule",
]


class BeatStartupDiagnosticTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("beat_startup_diagnostic", HELPER)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)

    def run_console(self, console, *, arguments=None, import_error=None,
                    cancel_error=None, arm_error=None, output=None):
        arguments = list(ARGUMENTS if arguments is None else arguments)
        expected_arguments = list(arguments)
        events, seen = [], []
        real_import = builtins.__import__
        stderr = io.StringIO() if output is None else output

        def imported(name, *args, **kwargs):
            if name == "celery.__main__":
                events.append("import")
                if import_error is not None:
                    raise import_error

                def main():
                    seen.append(list(sys.argv))
                    events.append("console")
                    return console()

                return types.SimpleNamespace(main=main)
            return real_import(name, *args, **kwargs)

        def armed(*args, **kwargs):
            events.append("arm")
            if arm_error is not None:
                raise arm_error

        def canceled():
            events.append("cancel")
            if cancel_error is not None:
                raise cancel_error

        self.events, self.seen, self.output = events, seen, stderr
        with patch.object(sys, "argv", arguments), patch.object(sys, "stderr", stderr), patch(
            "builtins.__import__", side_effect=imported,
        ), patch.object(self.helper.faulthandler, "dump_traceback_later", side_effect=armed) as arm, patch.object(
            self.helper.faulthandler, "cancel_dump_traceback_later", side_effect=canceled,
        ) as cancel:
            self.arm, self.cancel = arm, cancel
            original = sys.argv
            try:
                return self.helper.main()
            finally:
                self.assertIs(sys.argv, original)
                self.assertEqual(sys.argv, expected_arguments)

    def test_timer_is_armed_before_console_import_and_preserves_arguments(self):
        self.assertEqual(self.run_console(lambda: 7), 7)
        self.assertEqual(self.events, ["arm", "import", "console", "cancel"])
        self.arm.assert_called_once_with(60, repeat=True, file=self.output, exit=False)
        expected = list(ARGUMENTS)
        expected[0] = "/usr/local/bin/celery"
        self.assertEqual(self.seen, [expected])
        self.assertEqual(self.output.getvalue().splitlines(), [
            "beat_startup_diagnostic_armed", "beat_startup_diagnostic_console_imported",
        ])

    def test_success_none_and_system_exit_codes_are_preserved(self):
        self.assertIsNone(self.run_console(lambda: None))
        for code in (0, 37, 143):
            with self.subTest(code=code):
                def console():
                    raise SystemExit(code)

                with self.assertRaises(SystemExit) as raised:
                    self.run_console(console)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.events[-1], "cancel")

    def test_import_failure_cancels_timer_and_preserves_original_exception(self):
        error = ImportError("private startup details")
        with self.assertRaises(ImportError) as raised:
            self.run_console(lambda: None, import_error=error)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.events, ["arm", "import", "cancel"])
        self.assertNotIn("private", self.output.getvalue())

    def test_console_failure_is_preserved_and_diagnostic_output_is_constant(self):
        error = RuntimeError("private arguments and credential values")

        def console():
            raise error

        with self.assertRaises(RuntimeError) as raised:
            self.run_console(console)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.events[-1], "cancel")
        self.assertNotIn("private", self.output.getvalue())

    def test_console_argument_mutation_does_not_change_caller_list(self):
        def console():
            sys.argv.append("console-added-value")
            sys.argv[1] = "console-changed-value"
            return 0

        self.assertEqual(self.run_console(console), 0)

    def test_other_celery_commands_delegate_without_instrumentation(self):
        variants = [
            [ARGUMENTS[0], "--help"],
            [*ARGUMENTS[:3], "worker", *ARGUMENTS[4:]],
            [ARGUMENTS[0], "-A", "other.application", *ARGUMENTS[3:]],
            [*ARGUMENTS, "--extra"],
            [*ARGUMENTS[:5], "--scheduler=other.scheduler", ARGUMENTS[6]],
        ]
        for arguments in variants:
            with self.subTest(shape=len(arguments)):
                self.assertEqual(self.run_console(lambda: 23, arguments=arguments), 23)
                self.assertEqual(self.events, ["import", "console"])
                self.arm.assert_not_called()
                self.cancel.assert_not_called()
                self.assertEqual(self.output.getvalue(), "")
                expected = list(arguments)
                expected[0] = "/usr/local/bin/celery"
                self.assertEqual(self.seen, [expected])

    def test_environment_and_termination_handlers_are_unchanged(self):
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with patch.dict(os.environ, {"DIAGNOSTIC_PRIVATE_FIXTURE": "sentinel"}, clear=True):
            self.assertEqual(self.run_console(lambda: 0), 0)
            self.assertEqual(dict(os.environ), {"DIAGNOSTIC_PRIVATE_FIXTURE": "sentinel"})
        self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)
        self.assertNotIn("sentinel", self.output.getvalue())

    def test_cancel_failure_does_not_replace_workload_exit(self):
        for code in (None, 41):
            with self.subTest(code=code):
                if code is None:
                    self.assertIsNone(self.run_console(lambda: None, cancel_error=OSError("private cleanup")))
                else:
                    def console():
                        raise SystemExit(code)

                    with self.assertRaises(SystemExit) as raised:
                        self.run_console(console, cancel_error=OSError("private cleanup"))
                    self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.output.getvalue().splitlines()[-1], "beat_startup_diagnostic_cancel_failed")
                self.assertNotIn("private", self.output.getvalue())

    def test_failed_timer_arm_preserves_original_console_status(self):
        for status in (None, 19, "exit", "import_error"):
            with self.subTest(status=status):
                error = RuntimeError("private timer details")
                if status == "exit":
                    def console():
                        raise SystemExit(41)

                    with self.assertRaises(SystemExit) as raised:
                        self.run_console(console, arm_error=error)
                    self.assertEqual(raised.exception.code, 41)
                elif status == "import_error":
                    original = ImportError("private original startup details")
                    with self.assertRaises(ImportError) as raised:
                        self.run_console(lambda: None, arm_error=error, import_error=original)
                    self.assertIs(raised.exception, original)
                else:
                    self.assertEqual(self.run_console(lambda: status, arm_error=error), status)
                self.assertEqual(self.events, ["arm", "import"] + ([] if status == "import_error" else ["console"]))
                self.cancel.assert_not_called()
                self.assertEqual(self.output.getvalue().splitlines()[0], "beat_startup_diagnostic_arm_failed")
                self.assertNotIn("private", self.output.getvalue())

    def test_timer_arm_base_exception_is_preserved(self):
        original = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.run_console(lambda: None, arm_error=original)
        self.assertIs(raised.exception, original)
        self.assertEqual(self.events, ["arm"])
        self.cancel.assert_not_called()

    def test_broken_diagnostic_stream_does_not_replace_workload_status(self):
        class BrokenStream:
            def write(self, _value):
                raise OSError("private output details")

        self.assertEqual(self.run_console(lambda: 19, output=BrokenStream()), 19)
        self.cancel.assert_called_once()

    def test_launcher_interpreter_and_prefix_match_frozen_console_contract(self):
        self.assertEqual(HELPER.read_text().splitlines()[0], "#!/usr/local/bin/python3.12")
        source = ast.parse((ROOT / "backend/app/tasks/beat_watchdog.py").read_text())
        prefix = next(node.value for node in source.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "BEAT_COMMAND_PREFIX" for target in node.targets))
        self.assertEqual(ast.literal_eval(prefix), ("celery", *self.helper.BEAT_ARGUMENT_PREFIX))


if __name__ == "__main__":
    unittest.main()
