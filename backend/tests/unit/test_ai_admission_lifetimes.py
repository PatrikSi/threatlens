import time
import uuid
from types import SimpleNamespace

import pytest

from app.services import ai_provider_budgets as budgets
from app.services import outbound_deadline as deadlines
from app.services.ai_provider_client import AIIntegrationError


@pytest.fixture(autouse=True)
def disable_runtime_telemetry(monkeypatch):
    monkeypatch.setattr(deadlines, "record_runtime_event", lambda _event: None)


def test_expired_admission_before_call_never_sends_and_releases_reservation(monkeypatch):
    lease = budgets.AIProviderBudgetLease(uuid.uuid4(), time.monotonic() - 1)
    monkeypatch.setattr(budgets, "reserve_provider_budget", lambda *_args, **_kwargs: lease)
    settled = []
    monkeypatch.setattr(budgets, "settle_provider_budget", lambda *_args, **kwargs: settled.append(kwargs["result"]))
    calls = []
    with pytest.raises(AIIntegrationError) as failure:
        budgets.call_with_provider_budget(None, None, messages=[], requested_tokens=128,
            call=lambda *_args, **_kwargs: calls.append(True), call_kwargs={})
    assert calls == []
    assert failure.value.provider_io_outcome == "not_sent"
    assert failure.value.failure_category == "provider_admission_expired"
    assert settled == [failure.value]


def test_admission_keeps_absolute_deadline_through_provider_call(monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(deadlines, "time", SimpleNamespace(monotonic=lambda: clock.value))
    lease = budgets.AIProviderBudgetLease(uuid.uuid4(), 110.0)
    monkeypatch.setattr(budgets, "reserve_provider_budget", lambda *_args, **_kwargs: lease)
    settled = []
    monkeypatch.setattr(budgets, "settle_provider_budget", lambda *_args, **kwargs: settled.append(kwargs["result"]))
    calls = []

    def call(*_args, **_kwargs):
        calls.append(True)
        # A nested transport timeout cannot extend the lease after a pause.
        clock.value = 109.0
        with deadlines.outbound_deadline(300):
            assert deadlines.remaining_timeout() == 1
            clock.value = 111.0
            deadlines.check_outbound_deadline()

    with pytest.raises(AIIntegrationError) as failure:
        budgets.call_with_provider_budget(None, None, messages=[], requested_tokens=128, call=call, call_kwargs={})
    assert calls == [True]
    assert failure.value.provider_io_outcome == "ambiguous"
    assert failure.value.retryable is False
    assert settled == [failure.value]


def test_dns_deadline_expired_at_entry_is_definitely_before_http(monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(deadlines, "time", SimpleNamespace(monotonic=lambda: clock.value))
    resolved = []
    monkeypatch.setattr(deadlines.socket, "getaddrinfo", lambda *_args, **_kwargs: resolved.append(True))
    with deadlines.outbound_deadline(1):
        clock.value = 102.0
        with pytest.raises(deadlines.OutboundDNSDeadlineExceeded):
            deadlines.deadline_getaddrinfo("synthetic.example")
    assert resolved == []


def test_dns_deadline_expired_after_resolution_remains_definitely_unsent(monkeypatch):
    monkeypatch.setattr(deadlines.socket, "getaddrinfo", lambda *_args, **_kwargs: [])

    def expired():
        raise deadlines.OutboundDeadlineExceeded("synthetic deadline")

    with deadlines.outbound_deadline(10):
        monkeypatch.setattr(deadlines, "check_outbound_deadline", expired)
        with pytest.raises(deadlines.OutboundDNSDeadlineExceeded) as failure:
            deadlines.deadline_getaddrinfo("synthetic.example")
    assert isinstance(failure.value.__cause__, deadlines.OutboundDeadlineExceeded)
