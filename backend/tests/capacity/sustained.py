"""Paced closed-loop load: bounded concurrency, duration, and source growth."""

import math
import threading
import time


class SharedPacedLaneStart:
    """Admit all paced lanes together with one bounded, monotonic epoch."""

    def __init__(self, *, participants, timeout_seconds=30):
        if type(participants) is not int or participants < 1:
            raise ValueError("shared start gate requires positive participant count")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError("shared start gate timeout must be positive and at most 30 seconds")
        self._epoch = None
        self._barrier = threading.Barrier(participants, action=self._start,
                                          timeout=timeout_seconds)

    def _start(self):
        self._epoch = time.monotonic()

    def wait(self):
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError as error:
            raise RuntimeError("capacity shared start gate did not admit every lane") from error
        return self._epoch

    def abort(self):
        self._barrier.abort()


def paced_lane(
    operation, *, duration_seconds, interval_seconds, stop=None, initial_delay_seconds=0,
    start_gate=None,
):
    if start_gate is not None and stop is not None and stop.is_set():
        start_gate.abort()
        return 0
    started = time.monotonic() if start_gate is None else start_gate.wait()
    count = 0
    due = started + initial_delay_seconds
    while time.monotonic() - started < duration_seconds:
        if stop is not None and stop.is_set():
            break
        delay = min(
            max(0, due - time.monotonic()),
            max(0, duration_seconds - (time.monotonic() - started)),
        )
        if delay:
            time.sleep(delay)
        if time.monotonic() - started >= duration_seconds:
            break
        operation(count)
        count += 1
        # Do not catch up by creating a burst after a slow operation.
        due = max(due + interval_seconds, time.monotonic())
    return count
