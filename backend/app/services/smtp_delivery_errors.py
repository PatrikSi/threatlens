from __future__ import annotations

import smtplib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.integration_delivery_data_policy import (
        IntegrationDeliveryPolicyAudit,
    )


class SMTPDeliveryIneligibleError(RuntimeError):
    """The SMTP control plane changed before an external operation began."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data_policy_audit: IntegrationDeliveryPolicyAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data_policy_audit = data_policy_audit


class SMTPDeliveryTemporarilyIneligibleError(SMTPDeliveryIneligibleError):
    """A revoked delivery may become eligible again within its retry budget."""


class SMTPDeliveryDatabasePreflightError(RuntimeError):
    code = "smtp_preflight_database_unavailable"


class SMTPDeliverySourceContextError(ValueError):
    """Persisted SMTP source context cannot be trusted for external I/O."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SMTPDeliverySourceCompatibilityError(SMTPDeliverySourceContextError):
    """A newer event schema requires a compatible SMTP worker."""


def smtp_response_message(exc: smtplib.SMTPResponseException) -> str:
    return format_smtp_reply(exc.smtp_code, exc.smtp_error)


def recipients_refused_message(exc: smtplib.SMTPRecipientsRefused) -> str | None:
    return recipients_refused_mapping_message(exc.recipients)


def recipients_refused_error_code(exc: smtplib.SMTPRecipientsRefused) -> str:
    codes = [
        response[0]
        for response in exc.recipients.values()
        if isinstance(response, tuple) and len(response) == 2
    ]
    if any(_is_transient_smtp_response(code) for code in codes):
        return "transient_smtp_error"
    if codes and all(_is_permanent_smtp_response(code) for code in codes):
        return "recipient_rejected"
    return "smtp_error"


def recipients_refused_mapping_message(
    recipients: dict[str, tuple[int, bytes | str]],
) -> str | None:
    if not recipients:
        return None
    first = next(iter(recipients.values()))
    if not isinstance(first, tuple) or len(first) != 2:
        return str(recipients)
    code, message = first
    return format_smtp_reply(code, message)


def format_smtp_reply(code: int, message: object) -> str:
    if isinstance(message, bytes):
        message_text = message.decode("utf-8", errors="replace")
    else:
        message_text = str(message or "")
    return f"{code} {message_text}".strip()


def smtp_response_error_code(
    code: int,
    *,
    permanent_error_code: str = "smtp_rejected",
) -> str:
    if _is_transient_smtp_response(code):
        return "transient_smtp_error"
    if _is_permanent_smtp_response(code):
        return permanent_error_code
    return "smtp_error"


def _is_transient_smtp_response(code: object) -> bool:
    try:
        numeric_code = int(code)
    except (TypeError, ValueError, OverflowError):
        return False
    return 400 <= numeric_code < 500


def _is_permanent_smtp_response(code: object) -> bool:
    try:
        numeric_code = int(code)
    except (TypeError, ValueError, OverflowError):
        return False
    return 500 <= numeric_code < 600
