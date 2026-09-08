"""Paced closed-loop load: bounded concurrency, duration, and source growth."""

import time


def paced_lane(
    operation, *, duration_seconds, interval_seconds, stop=None, initial_delay_seconds=0
):
    started = time.monotonic()
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
