#!/usr/local/bin/python3.12
"""Explicit CI-only Celery PATH launcher; keep the deployed watchdog unchanged.

Copy this code-only file to an external read-only directory as ``celery`` and
prepend that directory to Beat's existing PATH. The interpreter and argv[0]
match the frozen backend image's original console script. Stack dumps contain
frames only and must remain private; this launcher never prints argument,
environment, SQL, heartbeat or fixture values.
"""
from __future__ import annotations

import faulthandler
import sys

DUMP_DELAY_SECONDS = 60
ORIGINAL_CONSOLE_PATH = "/usr/local/bin/celery"
BEAT_ARGUMENT_PREFIXES = frozenset(
    {
        # Retain the frozen runtime target used by the original diagnostic.
        ("-A", "app.tasks.celery_app.celery_app", "beat"),
        ("-A", "app.tasks.beat_app.beat_app", "beat"),
    }
)
BEAT_ARGUMENT_SUFFIX = (
    "--scheduler=app.tasks.beat_scheduler:WatchdogPersistentScheduler",
    "--schedule=/tmp/threatlens-celerybeat-schedule",
)


def watchdog_beat_invocation(arguments: list[str]) -> bool:
    return (
        len(arguments) == 7
        and tuple(arguments[1:4]) in BEAT_ARGUMENT_PREFIXES
        and arguments[4].startswith("--loglevel=")
        and tuple(arguments[5:]) == BEAT_ARGUMENT_SUFFIX
    )


def phase(marker: str) -> None:
    # Diagnostic reporting must not replace an original Celery exit or error.
    try:
        print(marker, file=sys.stderr, flush=True)
    except Exception:
        pass


def main():
    previous_arguments = sys.argv
    observed_arguments = list(previous_arguments)
    instrument = watchdog_beat_invocation(observed_arguments)
    observed_arguments[0] = ORIGINAL_CONSOLE_PATH
    sys.argv = observed_arguments
    armed = False
    try:
        if instrument:
            try:
                faulthandler.dump_traceback_later(
                    DUMP_DELAY_SECONDS, repeat=True, file=sys.stderr, exit=False
                )
            except Exception:
                phase("beat_startup_diagnostic_arm_failed")
            else:
                armed = True
                phase("beat_startup_diagnostic_armed")

        # Arm before Celery's console import, app loading and task imports.
        from celery.__main__ import main as celery_main

        if instrument:
            phase("beat_startup_diagnostic_console_imported")
        return celery_main()
    finally:
        try:
            if armed:
                try:
                    faulthandler.cancel_dump_traceback_later()
                except Exception:
                    phase("beat_startup_diagnostic_cancel_failed")
        finally:
            sys.argv = previous_arguments


if __name__ == "__main__":
    sys.exit(main())
