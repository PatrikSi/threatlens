from __future__ import annotations

import json
import logging
import logging.config
import re
import traceback
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.config import Settings


_LOG_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "threatlens_log_context",
    default=None,
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+\-/]+=*")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+)@", re.IGNORECASE)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|secret|token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"api[_-]?key|authorization|cookie|csrf|smtp[_-]?password|client[_-]?secret|authorization[_-]?code)\b[\"']?"
    r"\s*[=:]\s*[\"']?)([^\s,;&}\"']+)"
)
_SAFE_RECORD_FIELDS = (
    "request_id",
    "task_id",
    "task_name",
    "queue",
    "method",
    "path",
    "route",
    "status",
    "duration_ms",
    "client_ip",
)


def set_log_context(**values: object) -> Token:
    current = dict(_LOG_CONTEXT.get() or {})
    current.update({key: str(value) for key, value in values.items() if value is not None and str(value)})
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: Token) -> None:
    _LOG_CONTEXT.reset(token)


def get_log_context() -> dict[str, str]:
    return dict(_LOG_CONTEXT.get() or {})


def redact_log_text(value: object, *, max_chars: int = 20_000) -> str:
    text = str(value)
    text = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _URL_CREDENTIAL_PATTERN.sub(r"\g<scheme>[REDACTED]@", text)
    text = _SENSITIVE_VALUE_PATTERN.sub(r"\1[REDACTED]", text)
    if len(text) > max_chars:
        return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"
    return text


class DiagnosticContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_LOG_CONTEXT.get() or {}).items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class ThreatLensTextFormatter(logging.Formatter):
    def __init__(self, *, max_chars: int) -> None:
        super().__init__()
        self.max_chars = max_chars

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        context = " ".join(
            f"{field}={redact_log_text(getattr(record, field), max_chars=512)}"
            for field in _SAFE_RECORD_FIELDS
            if getattr(record, field, None) not in (None, "")
        )
        message = redact_log_text(record.getMessage(), max_chars=self.max_chars)
        rendered = f"{timestamp} level={record.levelname} logger={record.name}"
        if context:
            rendered = f"{rendered} {context}"
        rendered = f"{rendered} {message}"
        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            rendered = f"{rendered}\n{redact_log_text(exception_text, max_chars=self.max_chars)}"
        return rendered


class ThreatLensJsonFormatter(logging.Formatter):
    def __init__(self, *, max_chars: int) -> None:
        super().__init__()
        self.max_chars = max_chars

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_text(record.getMessage(), max_chars=self.max_chars),
        }
        for field in _SAFE_RECORD_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            payload["exception"] = redact_log_text(exception_text, max_chars=self.max_chars)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)


def configure_logging(settings: Settings, *, force: bool = True) -> None:
    formatter_class = (
        "app.core.logging_config.ThreatLensJsonFormatter"
        if settings.log_format == "json"
        else "app.core.logging_config.ThreatLensTextFormatter"
    )
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"diagnostic_context": {"()": "app.core.logging_config.DiagnosticContextFilter"}},
            "formatters": {
                "threatlens": {
                    "()": formatter_class,
                    "max_chars": settings.log_max_event_chars,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "level": "NOTSET",
                    "formatter": "threatlens",
                    "filters": ["diagnostic_context"],
                }
            },
            "root": {"level": settings.log_level, "handlers": ["console"]},
        }
    )
    if force:
        _route_framework_loggers_to_root(settings.log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO if settings.log_sql else logging.WARNING)
    for override in settings.log_level_overrides:
        logger_name, level = override.rsplit("=", 1)
        logging.getLogger(logger_name).setLevel(level)


def _route_framework_loggers_to_root(level: str) -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True
        framework_logger.setLevel(level)


def verbose_logging_enabled(settings: Settings) -> bool:
    return settings.log_detail == "verbose"


def log_configuration_summary(settings: Settings, *, logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger("threatlens.logging")
    target.info(
        "logging_configured level=%s overrides=%s format=%s detail=%s include_client_ip=%s slow_request_ms=%s sql=%s",
        settings.log_level,
        settings.log_level_overrides,
        settings.log_format,
        settings.log_detail,
        settings.log_include_client_ip,
        settings.log_slow_request_ms,
        settings.log_sql,
    )


__all__ = [
    "configure_logging",
    "get_log_context",
    "log_configuration_summary",
    "redact_log_text",
    "reset_log_context",
    "set_log_context",
    "verbose_logging_enabled",
]
