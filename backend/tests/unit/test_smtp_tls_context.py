"""Verify SMTP TLS policy without opening a network connection."""

import smtplib
import socket
import ssl
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import smtp_transport


@pytest.mark.parametrize("transport_path", ["ssl_tls", "starttls_fenced", "starttls_unfenced"])
@pytest.mark.parametrize(
    "default_minimum",
    [ssl.TLSVersion.MINIMUM_SUPPORTED, ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3],
)
def test_smtp_tls_context_enforces_minimum_and_preserves_verification(
    monkeypatch, transport_path, default_minimum,
):
    original_context_factory = ssl.create_default_context
    created_contexts = []
    supplied_contexts = []

    def default_context():
        context = original_context_factory()
        context.minimum_version = default_minimum
        created_contexts.append(context)
        return context

    monkeypatch.setattr(smtp_transport.ssl, "create_default_context", default_context)
    forbid_network = Mock(side_effect=AssertionError("Unexpected SMTP network access"))
    monkeypatch.setattr(socket, "socket", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    active = SimpleNamespace(
        security="ssl_tls" if transport_path == "ssl_tls" else "starttls",
        host="smtp.example.com",
        username=None,
    )
    deadline = time.perf_counter() + 10

    if transport_path == "ssl_tls":
        sentinel = SimpleNamespace()

        def ssl_client(*, context, timeout, local_hostname):
            supplied_contexts.append(context)
            assert 0 < timeout <= 10
            assert local_hostname
            return sentinel

        monkeypatch.setattr(smtp_transport.smtplib, "SMTP_SSL", ssl_client)
        assert smtp_transport._new_smtp_client(active, operation_deadline=deadline) is sentinel
    elif transport_path == "starttls_fenced":
        # An unconnected SMTP object exercises the real deadline-aware branch.
        server = smtplib.SMTP(local_hostname="smtp-fixture")
        monkeypatch.setattr(server, "ehlo", Mock(return_value=(250, b"OK")))

        def start_tls(smtp, *, context, operation_deadline):
            assert smtp is server
            assert operation_deadline == deadline
            supplied_contexts.append(context)
            return 220, b"ready"

        monkeypatch.setattr(smtp_transport, "start_smtp_tls", start_tls)
        smtp_transport.prepare_smtp_session(server, active, operation_deadline=deadline)
    else:
        def start_tls(*, context):
            supplied_contexts.append(context)
            return 220, b"ready"

        server = SimpleNamespace(ehlo=Mock(return_value=(250, b"OK")), starttls=start_tls)
        smtp_transport.prepare_smtp_session(server, active)

    assert len(created_contexts) == len(supplied_contexts) == 1
    context = supplied_contexts[0]
    assert context is created_contexts[0]
    assert context.minimum_version >= max(default_minimum, ssl.TLSVersion.TLSv1_2)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    forbid_network.assert_not_called()
