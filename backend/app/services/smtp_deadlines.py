from __future__ import annotations

import signal
import smtplib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from threading import current_thread, main_thread

from app.core.config import get_settings

SMTP_MIN_OPERATION_SECONDS = 0.000_001
SMTP_FENCED_IO_MAX_SECONDS = 15.0


def fenced_smtp_io_timeout_seconds(
    configured_timeout_seconds: int | float,
) -> float:
    statement_timeout_seconds = max(
        SMTP_MIN_OPERATION_SECONDS,
        float(get_settings().database_statement_timeout_ms) / 1_000,
    )
    return min(
        max(SMTP_MIN_OPERATION_SECONDS, float(configured_timeout_seconds)),
        SMTP_FENCED_IO_MAX_SECONDS,
        statement_timeout_seconds * (2 / 3),
    )


def set_smtp_operation_timeout(
    server: smtplib.SMTP,
    operation_deadline: float | None,
) -> None:
    if operation_deadline is None:
        return
    remaining = remaining_smtp_operation_seconds(operation_deadline)
    transport = getattr(server, "sock", None)
    if transport is not None and hasattr(transport, "settimeout"):
        transport.settimeout(remaining)


def remaining_smtp_operation_seconds(operation_deadline: float) -> float:
    remaining = operation_deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("SMTP operation exceeded its total timeout budget.")
    return max(SMTP_MIN_OPERATION_SECONDS, remaining)


def install_smtp_deadline_hooks(
    server: smtplib.SMTP,
    operation_deadline: float,
) -> None:
    for method_name in ("getreply", "send"):
        original = getattr(server, method_name, None)
        if not callable(original):
            continue

        def _with_deadline(*args, _original=original, **kwargs):
            set_smtp_operation_timeout(server, operation_deadline)
            return _original(*args, **kwargs)

        setattr(server, method_name, _with_deadline)


@contextmanager
def enforce_smtp_operation_deadline(
    operation_deadline: float,
) -> Iterator[None]:
    if (
        current_thread() is not main_thread()
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        yield
        return

    remaining = remaining_smtp_operation_seconds(operation_deadline)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    if previous_delay > 0 and previous_delay <= remaining:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    timer_started_at = time.monotonic()

    def _deadline_exceeded(_signum, _frame) -> None:
        raise TimeoutError("SMTP operation exceeded its total timeout budget.")

    signal.signal(signal.SIGALRM, _deadline_exceeded)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            elapsed = max(0.0, time.monotonic() - timer_started_at)
            signal.setitimer(
                signal.ITIMER_REAL,
                max(SMTP_MIN_OPERATION_SECONDS, previous_delay - elapsed),
                previous_interval,
            )
