import json
import logging

from app.core.logging_config import (
    ThreatLensJsonFormatter,
    ThreatLensTextFormatter,
    redact_log_text,
    reset_log_context,
    set_log_context,
)


def _record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="threatlens.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_redact_log_text_removes_common_credentials():
    rendered = redact_log_text(
        "authorization=Bearer abc.def password=hunter2 "
        "database=postgresql://user:db-secret@db/threatlens client_secret=oidc-secret"
    )

    assert "abc.def" not in rendered
    assert "hunter2" not in rendered
    assert "db-secret" not in rendered
    assert "oidc-secret" not in rendered
    assert rendered.count("[REDACTED]") >= 4


def test_text_formatter_adds_diagnostic_context_without_secret_values():
    token = set_log_context(request_id="request-123")
    try:
        record = _record("request failed token=%s", "private-token")
        for key, value in {"request_id": "request-123", "method": "GET", "path": "/v1/test"}.items():
            setattr(record, key, value)
        rendered = ThreatLensTextFormatter(max_chars=20_000).format(record)
    finally:
        reset_log_context(token)

    assert "request_id=request-123" in rendered
    assert "method=GET" in rendered
    assert "private-token" not in rendered


def test_json_formatter_emits_machine_parseable_context():
    record = _record("request_complete")
    record.request_id = "request-456"
    record.status = 503
    record.duration_ms = 42.5

    payload = json.loads(ThreatLensJsonFormatter(max_chars=20_000).format(record))

    assert payload["message"] == "request_complete"
    assert payload["request_id"] == "request-456"
    assert payload["status"] == 503
    assert payload["duration_ms"] == 42.5
