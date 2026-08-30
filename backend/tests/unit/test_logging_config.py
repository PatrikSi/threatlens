import json
import logging
from contextvars import copy_context
from types import SimpleNamespace

from app.core import logging_config
from app.core.logging_config import (
    ThreatLensJsonFormatter,
    ThreatLensTextFormatter,
    configure_logging,
    get_log_context,
    remove_log_context,
    redact_log_text,
    reset_log_context,
    set_log_context,
    update_log_context,
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
        "database=postgresql://user:db-secret@db/threatlens client_secret=oidc-secret "
        "redirect=https://idp/callback?access_token=oauth-token "
        "payload={'refresh_token': 'refresh-secret'} json={\"api_key\": \"json-secret\"}"
    )

    assert "abc.def" not in rendered
    assert "hunter2" not in rendered
    assert "db-secret" not in rendered
    assert "oidc-secret" not in rendered
    assert "oauth-token" not in rendered
    assert "refresh-secret" not in rendered
    assert "json-secret" not in rendered
    assert rendered.count("[REDACTED]") >= 7


def test_text_formatter_adds_diagnostic_context_without_secret_values():
    token = set_log_context(request_id="request-123")
    try:
        record = _record("request failed token=%s", "private-token")
        for key, value in {
            "request_id": "request-123",
            "method": "GET",
            "path": "/v1/test",
        }.items():
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


def test_log_context_updates_cross_copied_sync_worker_contexts():
    token = set_log_context(request_id="request-shared")
    try:
        worker_context = copy_context()
        worker_context.run(
            update_log_context,
            credential_kind="api_token",
            credential_id="00000000-0000-4000-8000-000000000001",
        )
        current = get_log_context()
    finally:
        reset_log_context(token)

    assert current["request_id"] == "request-shared"
    assert current["credential_kind"] == "api_token"
    assert current["credential_id"] == "00000000-0000-4000-8000-000000000001"


def test_log_context_can_remove_superseded_authorization_provenance():
    token = set_log_context(
        request_id="request-clear",
        authorization_elevation_ids="elevation-1",
    )
    try:
        remove_log_context("authorization_elevation_ids")
        current = get_log_context()
    finally:
        reset_log_context(token)

    assert current == {"request_id": "request-clear"}


def test_per_logger_override_can_be_more_verbose_than_root(monkeypatch):
    configured: dict[str, object] = {}
    logger_levels: dict[str, str] = {}

    monkeypatch.setattr(
        logging_config.logging.config,
        "dictConfig",
        lambda value: configured.update(value),
    )
    for logger_name in ("sqlalchemy.engine", "app.services.oidc_client"):
        target = logging.getLogger(logger_name)
        monkeypatch.setattr(
            target,
            "setLevel",
            lambda level, name=logger_name: logger_levels.__setitem__(name, level),
        )
    settings = SimpleNamespace(
        log_format="text",
        log_max_event_chars=20_000,
        log_level="INFO",
        log_sql=False,
        log_level_overrides=["app.services.oidc_client=DEBUG"],
    )

    configure_logging(settings, force=False)

    assert configured["handlers"]["console"]["level"] == "NOTSET"
    assert configured["root"]["level"] == "INFO"
    assert logger_levels["app.services.oidc_client"] == "DEBUG"
