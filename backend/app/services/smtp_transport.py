from __future__ import annotations

import smtplib
import socket
import ssl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import BoundedSemaphore, Event, Thread, Timer

from app.services.integration_storage import ActiveSMTPSettings
from app.services.smtp_deadlines import (
    SMTP_MIN_OPERATION_SECONDS,
    remaining_smtp_operation_seconds,
    set_smtp_operation_timeout,
)

SMTP_DNS_RESOLVER_CONCURRENCY = 4
_smtp_dns_resolver_slots = BoundedSemaphore(SMTP_DNS_RESOLVER_CONCURRENCY)


class SMTPStartTLSNotSupportedError(smtplib.SMTPNotSupportedError):
    pass


@dataclass(frozen=True)
class _ResolvedSMTPAddress:
    family: int
    socket_type: int
    protocol: int
    socket_address: tuple


def open_smtp_connection(active: ActiveSMTPSettings) -> smtplib.SMTP:
    timeout_seconds = max(SMTP_MIN_OPERATION_SECONDS, float(active.timeout_seconds))
    operation_deadline = time.perf_counter() + timeout_seconds
    addresses = _resolve_smtp_addresses(
        active.host or "",
        active.port,
        operation_deadline=operation_deadline,
    )
    last_error: Exception | None = None
    for address in addresses:
        server = _new_smtp_client(active, operation_deadline=operation_deadline)
        try:
            with interrupt_smtp_transport_at_deadline(
                server,
                operation_deadline,
            ):
                code, message = _connect_resolved_smtp_address(
                    server,
                    address,
                    operation_deadline=operation_deadline,
                )
            remaining_smtp_operation_seconds(operation_deadline)
            if code != 220:
                raise smtplib.SMTPConnectError(code, message)
            return server
        except (OSError, smtplib.SMTPException, TimeoutError) as exc:
            last_error = exc
            _close_transport(server)
            if smtp_operation_deadline_expired(operation_deadline):
                raise TimeoutError(
                    "SMTP operation exceeded its total timeout budget."
                ) from exc
    if last_error is not None:
        raise last_error
    raise OSError("SMTP host did not resolve to a usable network address.")


def prepare_smtp_session(
    server: smtplib.SMTP,
    active: ActiveSMTPSettings,
    *,
    lease_heartbeat: Callable[[int, ActiveSMTPSettings], None] | None = None,
    operation_deadline: float | None = None,
) -> None:
    renew_smtp_operation_lease(lease_heartbeat, active)
    set_smtp_operation_timeout(server, operation_deadline)
    _require_smtp_response(server.ehlo(), expected_code=250)
    if active.security == "starttls":
        renew_smtp_operation_lease(lease_heartbeat, active)
        set_smtp_operation_timeout(server, operation_deadline)
        if operation_deadline is not None and isinstance(server, smtplib.SMTP):
            start_smtp_tls(
                server,
                context=ssl.create_default_context(),
                operation_deadline=operation_deadline,
            )
        else:
            _require_smtp_response(
                server.starttls(context=ssl.create_default_context()),
                expected_code=220,
            )
        renew_smtp_operation_lease(lease_heartbeat, active)
        set_smtp_operation_timeout(server, operation_deadline)
        _require_smtp_response(server.ehlo(), expected_code=250)
    if active.username:
        renew_smtp_operation_lease(lease_heartbeat, active)
        set_smtp_operation_timeout(server, operation_deadline)
        server.login(active.username, active.password or "")


def renew_smtp_operation_lease(
    lease_heartbeat: Callable[[int, ActiveSMTPSettings], None] | None,
    active: ActiveSMTPSettings,
) -> None:
    if lease_heartbeat is None:
        return
    lease_heartbeat(
        max(30, (max(1, int(active.timeout_seconds)) * 2) + 15),
        active,
    )


@contextmanager
def interrupt_smtp_transport_at_deadline(
    server: smtplib.SMTP,
    operation_deadline: float,
) -> Iterator[None]:
    remaining = remaining_smtp_operation_seconds(operation_deadline)
    timer = Timer(remaining, _interrupt_transport, args=(server,))
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


def smtp_operation_deadline_expired(operation_deadline: float) -> bool:
    return time.perf_counter() >= operation_deadline


def _resolve_smtp_addresses(
    host: str,
    port: int,
    *,
    operation_deadline: float,
) -> tuple[_ResolvedSMTPAddress, ...]:
    if not host:
        raise OSError("SMTP host is required.")
    completed = Event()
    resolved: list[_ResolvedSMTPAddress] = []
    errors: list[Exception] = []

    if not _smtp_dns_resolver_slots.acquire(
        timeout=remaining_smtp_operation_seconds(operation_deadline)
    ):
        raise TimeoutError(
            "SMTP DNS resolver capacity is exhausted by earlier timed-out lookups."
        )

    def _resolve() -> None:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            resolved.extend(
                _ResolvedSMTPAddress(
                    family=family,
                    socket_type=socket_type,
                    protocol=protocol,
                    socket_address=socket_address,
                )
                for family, socket_type, protocol, _canonical_name, socket_address in records
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            _smtp_dns_resolver_slots.release()
            completed.set()

    resolver = Thread(target=_resolve, name="smtp-dns-resolution", daemon=True)
    try:
        resolver.start()
    except Exception:
        _smtp_dns_resolver_slots.release()
        raise
    if not completed.wait(remaining_smtp_operation_seconds(operation_deadline)):
        raise TimeoutError("SMTP DNS resolution exceeded its total timeout budget.")
    if errors:
        raise errors[0]
    addresses = tuple(dict.fromkeys(resolved))
    if not addresses:
        raise OSError("SMTP host did not resolve to a usable network address.")
    return addresses


def _new_smtp_client(
    active: ActiveSMTPSettings,
    *,
    operation_deadline: float,
) -> smtplib.SMTP:
    timeout = remaining_smtp_operation_seconds(operation_deadline)
    local_hostname = socket.gethostname() or "localhost"
    if active.security == "ssl_tls":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            timeout=timeout,
            context=ssl.create_default_context(),
            local_hostname=local_hostname,
        )
    else:
        server = smtplib.SMTP(
            timeout=timeout,
            local_hostname=local_hostname,
        )
    server._host = active.host or ""
    return server


def _connect_resolved_smtp_address(
    server: smtplib.SMTP,
    address: _ResolvedSMTPAddress,
    *,
    operation_deadline: float,
) -> tuple[int, bytes]:
    timeout = remaining_smtp_operation_seconds(operation_deadline)
    transport = socket.socket(
        address.family,
        address.socket_type,
        address.protocol,
    )
    server.sock = transport
    server.file = None
    transport.settimeout(timeout)
    transport.connect(address.socket_address)
    if isinstance(server, smtplib.SMTP_SSL):
        tls_transport = server.context.wrap_socket(
            transport,
            server_hostname=server._host,
            do_handshake_on_connect=False,
        )
        server.sock = tls_transport
        tls_transport.settimeout(remaining_smtp_operation_seconds(operation_deadline))
        tls_transport.do_handshake()
    return server.getreply()


def start_smtp_tls(
    server: smtplib.SMTP,
    *,
    context: ssl.SSLContext,
    operation_deadline: float,
) -> tuple[int, bytes]:
    server.ehlo_or_helo_if_needed()
    if not server.has_extn("starttls"):
        raise SMTPStartTLSNotSupportedError(
            "STARTTLS extension not supported by server."
        )
    response, reply = server.docmd("STARTTLS")
    if response != 220:
        raise smtplib.SMTPResponseException(response, reply)
    transport = server.sock
    if transport is None:
        raise smtplib.SMTPServerDisconnected("SMTP connection is not available")
    tls_transport = context.wrap_socket(
        transport,
        server_hostname=server._host,
        do_handshake_on_connect=False,
    )
    server.sock = tls_transport
    server.file = None
    tls_transport.settimeout(remaining_smtp_operation_seconds(operation_deadline))
    tls_transport.do_handshake()
    server.helo_resp = None
    server.ehlo_resp = None
    server.esmtp_features = {}
    server.does_esmtp = False
    return response, reply


def _require_smtp_response(
    response: tuple[int, bytes | str],
    *,
    expected_code: int,
) -> None:
    code, message = response
    if code != expected_code:
        raise smtplib.SMTPResponseException(code, message)


def _interrupt_transport(server: smtplib.SMTP) -> None:
    transport = getattr(server, "sock", None)
    if transport is None:
        return
    try:
        transport.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        transport.close()
    except OSError:
        pass


def _close_transport(server: smtplib.SMTP) -> None:
    try:
        server.close()
    except (AttributeError, OSError):
        pass
