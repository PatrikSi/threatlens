"""Monotonic budgets for synchronous outbound work, without detached HTTP calls."""
from __future__ import annotations

import socket
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

import httpx

_deadline: ContextVar[float | None] = ContextVar("outbound_deadline", default=None)
# A stuck system resolver must not create an unbounded queue or thread population.
_resolver_slots = threading.BoundedSemaphore(8)


def _reset_resolver_slots_after_fork() -> None:
    global _resolver_slots
    _resolver_slots = threading.BoundedSemaphore(8)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_resolver_slots_after_fork)


class OutboundDeadlineExceeded(httpx.TimeoutException):
    """The whole request budget expired; this does not prove it was not sent."""


@contextmanager
def outbound_deadline(seconds: float) -> Iterator[None]:
    previous = _deadline.get()
    deadline = time.monotonic() + seconds
    token = _deadline.set(min(previous, deadline) if previous is not None else deadline)
    try:
        check_outbound_deadline()
        yield
    finally:
        _deadline.reset(token)


def remaining_timeout(timeout: float | None = None) -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OutboundDeadlineExceeded("outbound request total deadline exceeded")
    return min(timeout, remaining) if timeout is not None else remaining


def check_outbound_deadline() -> None:
    remaining_timeout()


def deadline_getaddrinfo(host: str) -> list:
    """Only DNS may outlive its caller, with at most eight outstanding resolvers.

    No provider request is delegated: policy/task fences remain held until all
    synchronous HTTP I/O has stopped. The system resolver is not cancellable.
    """
    remaining = remaining_timeout()
    if remaining is None:
        return socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    slots = _resolver_slots
    if not slots.acquire(timeout=remaining):
        raise OutboundDeadlineExceeded("outbound DNS capacity deadline exceeded")
    finished = threading.Event()
    result: list = []
    failure: list[BaseException] = []

    def resolve() -> None:
        try:
            result.extend(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
        except BaseException as exc:
            failure.append(exc)
        finally:
            slots.release()
            finished.set()

    try:
        threading.Thread(target=resolve, name="outbound-dns", daemon=True).start()
    except BaseException:
        slots.release()
        raise
    if not finished.wait(timeout=remaining_timeout()):
        raise OutboundDeadlineExceeded("outbound DNS resolution deadline exceeded")
    check_outbound_deadline()
    if failure:
        raise failure[0]
    return result
