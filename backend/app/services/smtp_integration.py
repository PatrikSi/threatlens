from __future__ import annotations

import smtplib
import socket
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from app.schemas.integration import SMTPTestResponse
from app.services.integration_storage import ActiveSMTPSettings


def test_smtp_integration(active: ActiveSMTPSettings, *, recipient_email: str | None) -> SMTPTestResponse:
    action = "send" if recipient_email else "connection"
    started_at = time.perf_counter()
    tested_at = datetime.now(timezone.utc)

    validation_error = _validate_test_settings(active, recipient_email=recipient_email)
    if validation_error:
        return _test_response(
            success=False,
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="validation_error",
            error=validation_error,
            server_message=None,
        )

    assert active.host is not None
    try:
        with _open_smtp(active) as server:
            server_message = _login_and_test(server, active, recipient_email=recipient_email)
            return _test_response(
                success=True,
                action=action,
                started_at=started_at,
                tested_at=tested_at,
                recipient_email=recipient_email,
                error_code=None,
                error=None,
                server_message=server_message,
            )
    except smtplib.SMTPAuthenticationError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="auth_error",
            error="SMTP authentication failed.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPRecipientsRefused as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="recipient_rejected",
            error="SMTP server rejected the test recipient.",
            server_message=_recipients_refused_message(exc),
        )
    except smtplib.SMTPSenderRefused as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="sender_rejected",
            error="SMTP server rejected the configured sender.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPConnectError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="connect_error",
            error="SMTP server refused the connection.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPServerDisconnected as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="connection_closed",
            error="SMTP server closed the connection unexpectedly.",
            server_message=str(exc) or None,
        )
    except smtplib.SMTPResponseException as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code=_smtp_response_error_code(exc.smtp_code),
            error="SMTP server returned an error.",
            server_message=_smtp_response_message(exc),
        )
    except (TimeoutError, socket.timeout):
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="timeout",
            error=f"SMTP test timed out after {active.timeout_seconds}s.",
            server_message=None,
        )
    except ssl.SSLError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="tls_error",
            error="SMTP TLS negotiation failed.",
            server_message=str(exc),
        )
    except OSError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="connection_error",
            error="SMTP connection failed.",
            server_message=str(exc),
        )


def _open_smtp(active: ActiveSMTPSettings):
    timeout = max(1, int(active.timeout_seconds))
    if active.security == "ssl_tls":
        return smtplib.SMTP_SSL(active.host, active.port, timeout=timeout, context=ssl.create_default_context())
    return smtplib.SMTP(active.host, active.port, timeout=timeout)


def _login_and_test(server: smtplib.SMTP, active: ActiveSMTPSettings, *, recipient_email: str | None) -> str | None:
    server.ehlo()
    if active.security == "starttls":
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    if active.username:
        server.login(active.username, active.password or "")
    if recipient_email:
        refused = server.send_message(_build_test_message(active, recipient_email))
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return "Test email accepted by SMTP server."

    code, message = server.noop()
    return _format_smtp_reply(code, message)


def _build_test_message(active: ActiveSMTPSettings, recipient_email: str) -> EmailMessage:
    message = EmailMessage()
    assert active.from_email is not None
    message["From"] = formataddr((active.from_name or "ThreatLens", active.from_email))
    message["To"] = recipient_email
    message["Subject"] = "ThreatLens SMTP test"
    message.set_content(
        "This is a ThreatLens SMTP integration test message. If you received it, the configured SMTP server accepted delivery."
    )
    return message


def _validate_test_settings(active: ActiveSMTPSettings, *, recipient_email: str | None) -> str | None:
    if not active.host:
        return "SMTP host is required before testing."
    if active.username and not active.password:
        return "SMTP password is required when a username is configured."
    if recipient_email and not active.from_email:
        return "Sender email is required before sending a test email."
    return None


def _failure_response(
    *,
    action: str,
    started_at: float,
    tested_at: datetime,
    recipient_email: str | None,
    error_code: str,
    error: str,
    server_message: str | None,
) -> SMTPTestResponse:
    return _test_response(
        success=False,
        action=action,
        started_at=started_at,
        tested_at=tested_at,
        recipient_email=recipient_email,
        error_code=error_code,
        error=error,
        server_message=server_message,
    )


def _test_response(
    *,
    success: bool,
    action: str,
    started_at: float,
    tested_at: datetime,
    recipient_email: str | None,
    error_code: str | None,
    error: str | None,
    server_message: str | None,
) -> SMTPTestResponse:
    return SMTPTestResponse(
        success=success,
        action=action,
        duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        recipient_email=recipient_email,
        error_code=error_code,
        error=error,
        server_message=server_message,
        tested_at=tested_at,
        used_unsaved_settings=False,
    )


def _smtp_response_message(exc: smtplib.SMTPResponseException) -> str:
    return _format_smtp_reply(exc.smtp_code, exc.smtp_error)


def _recipients_refused_message(exc: smtplib.SMTPRecipientsRefused) -> str | None:
    if not exc.recipients:
        return None
    first = next(iter(exc.recipients.values()))
    if not isinstance(first, tuple) or len(first) != 2:
        return str(exc.recipients)
    code, message = first
    return _format_smtp_reply(code, message)


def _format_smtp_reply(code: int, message) -> str:
    if isinstance(message, bytes):
        message_text = message.decode("utf-8", errors="replace")
    else:
        message_text = str(message or "")
    return f"{code} {message_text}".strip()


def _smtp_response_error_code(code: int) -> str:
    if 400 <= int(code) < 500:
        return "transient_smtp_error"
    if int(code) >= 500:
        return "smtp_rejected"
    return "smtp_error"
