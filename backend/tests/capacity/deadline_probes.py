"""Observed deadlines against bounded synthetic DNS and loopback socket delays."""

import socket
import time
from unittest.mock import patch

import httpx

from app.services.outbound_deadline import deadline_getaddrinfo, outbound_deadline
from app.services.safe_fetch import build_safe_http_client


def observe_deadlines(metrics, base, *, repetitions=3, timeout=0.1):
    for _ in range(repetitions):
        for kind in ("dns", "headers"):
            started = time.monotonic()
            with metrics.operation("deadline:" + kind):
                try:
                    with outbound_deadline(timeout):
                        if kind == "dns":
                            original_resolver = socket.getaddrinfo

                            def delayed_resolver(host, *args, _original=original_resolver, **kwargs):
                                if host != "capacity-deadline.invalid":
                                    return _original(host, *args, **kwargs)
                                time.sleep(timeout * 3)
                                return [
                                    (
                                        socket.AF_INET,
                                        socket.SOCK_STREAM,
                                        6,
                                        "",
                                        ("127.0.0.1", 0),
                                    )
                                ]

                            # Only the DNS primitive is delayed; the real deadline,
                            # bounded resolver pool, and cancellation code execute.
                            with patch(
                                "app.services.outbound_deadline.socket.getaddrinfo",
                                delayed_resolver,
                            ):
                                deadline_getaddrinfo("capacity-deadline.invalid")
                        else:
                            with build_safe_http_client(
                                timeout=5, allow_private_network=True
                            ) as client:
                                client.get(base + "/deadline/headers")
                except httpx.TimeoutException:
                    metrics.outcome("deadline_" + kind + "_observed")
                else:
                    raise AssertionError(kind + " deadline did not fire")
            assert time.monotonic() - started < timeout + 0.75, (
                kind + " deadline cancellation exceeded grace"
            )
