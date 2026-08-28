from __future__ import annotations

import smtplib
import socket
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import unescape
from re import compile as compile_regex
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.feed import Feed
from app.models.integration import IntegrationInstance
from app.models.item import Item
from app.schemas.integration import SMTPTestResponse
from app.schemas.notification import NotificationEventType
from app.services.integration_storage import (
    SMTP_SYSTEM_KEY,
    ActiveSMTPSettings,
    SMTPSecretError,
    acquire_smtp_configuration_read_lock,
    build_active_smtp_settings,
    get_smtp_credential_source,
    smtp_settings_response_from_model,
)
from app.services.notification_webhooks import (
    AlertMatchContext,
    DailyDigestContext,
    FailedWebhookContext,
    TemplateRenderError,
    render_notification_template_text,
)
from app.services.smtp_deadlines import (
    SMTP_MIN_OPERATION_SECONDS,
    enforce_smtp_operation_deadline as _enforce_smtp_operation_deadline,
    fenced_smtp_io_timeout_seconds as _fenced_smtp_io_timeout_seconds,
    install_smtp_deadline_hooks as _install_smtp_deadline_hooks,
    remaining_smtp_operation_seconds as _remaining_smtp_operation_seconds,
    set_smtp_operation_timeout as _set_smtp_operation_timeout,
)
from app.services.smtp_delivery_history import (
    SMTP_DELIVERY_AUDIT_ACTION as SMTP_DELIVERY_AUDIT_ACTION,
    apply_smtp_delivery_result as _apply_smtp_delivery_result,
    record_smtp_delivery_audit as _record_smtp_delivery_audit,
    smtp_delivery_attempt_skip_reason as _smtp_delivery_attempt_skip_reason,
    smtp_delivery_dedupe_key as _smtp_delivery_dedupe_key,
)
from app.services.smtp_delivery_errors import (
    SMTPDeliveryDatabasePreflightError,
    format_smtp_reply as _format_smtp_reply,
    recipients_refused_error_code as _recipients_refused_error_code,
    recipients_refused_mapping_message as _recipients_refused_mapping_message,
    recipients_refused_message as _recipients_refused_message,
    smtp_response_error_code as _smtp_response_error_code,
    smtp_response_message as _smtp_response_message,
)
from app.services.smtp_delivery_results import (
    SMTPDispatchResult,
    SMTPNotificationResult,
    notification_failure_result as _notification_failure_result,
    notification_smtp_exception_result as _notification_smtp_exception_result,
    notification_timeout_result as _notification_timeout_result,
)
from app.services.smtp_transport import (
    SMTPStartTLSNotSupportedError,
    interrupt_smtp_transport_at_deadline as _interrupt_smtp_transport_at_deadline,
    open_smtp_connection as _open_smtp_connection,
    prepare_smtp_session as _prepare_smtp_session,
    renew_smtp_operation_lease as _renew_smtp_operation_lease,
    smtp_operation_deadline_expired as _smtp_operation_deadline_expired,
)
from app.services.smtp_test_results import (
    smtp_test_failure_response as _failure_response,
    smtp_test_response as _test_response,
    smtp_test_timeout_response as _test_timeout_response,
)
from app.services.smtp_delivery_eligibility import persisted_smtp_settings_heartbeat

HTML_TAG_PATTERN = compile_regex(r"<[^>]+>")


def test_smtp_integration(
    active: ActiveSMTPSettings, *, recipient_email: str | None
) -> SMTPTestResponse:
    action = "send" if recipient_email else "connection"
    started_at = time.perf_counter()
    operation_deadline = started_at + max(
        SMTP_MIN_OPERATION_SECONDS, float(active.timeout_seconds)
    )
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
        with _enforce_smtp_operation_deadline(operation_deadline):
            connection_settings = replace(
                active,
                timeout_seconds=_remaining_smtp_operation_seconds(operation_deadline),
            )
            server = _open_smtp(connection_settings)
            _install_smtp_deadline_hooks(server, operation_deadline)
            try:
                with _interrupt_smtp_transport_at_deadline(
                    server,
                    operation_deadline,
                ):
                    server_message = _login_and_test(
                        server,
                        active,
                        recipient_email=recipient_email,
                        operation_deadline=operation_deadline,
                    )
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
            finally:
                _close_smtp_transport(server)
    except SMTPStartTLSNotSupportedError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="starttls_not_supported",
            error="SMTP server does not support the required STARTTLS capability.",
            server_message=str(exc) or None,
        )
    except smtplib.SMTPNotSupportedError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code="smtp_capability_unsupported",
            error="SMTP server does not support a required capability.",
            server_message=str(exc) or None,
        )
    except smtplib.SMTPAuthenticationError as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code=_smtp_response_error_code(
                exc.smtp_code,
                permanent_error_code="auth_error",
            ),
            error="SMTP authentication failed.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPRecipientsRefused as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code=_recipients_refused_error_code(exc),
            error="SMTP server rejected the test recipient.",
            server_message=_recipients_refused_message(exc),
        )
    except smtplib.SMTPSenderRefused as exc:
        return _failure_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            error_code=_smtp_response_error_code(
                exc.smtp_code,
                permanent_error_code="sender_rejected",
            ),
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
        if _smtp_operation_deadline_expired(operation_deadline):
            return _test_timeout_response(
                action=action,
                started_at=started_at,
                tested_at=tested_at,
                recipient_email=recipient_email,
                active=active,
            )
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
        return _test_timeout_response(
            action=action,
            started_at=started_at,
            tested_at=tested_at,
            recipient_email=recipient_email,
            active=active,
        )
    except ssl.SSLError as exc:
        if _smtp_operation_deadline_expired(operation_deadline):
            return _test_timeout_response(
                action=action,
                started_at=started_at,
                tested_at=tested_at,
                recipient_email=recipient_email,
                active=active,
            )
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
        if _smtp_operation_deadline_expired(operation_deadline):
            return _test_timeout_response(
                action=action,
                started_at=started_at,
                tested_at=tested_at,
                recipient_email=recipient_email,
                active=active,
            )
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
    return _open_smtp_connection(active)


def _login_and_test(
    server: smtplib.SMTP,
    active: ActiveSMTPSettings,
    *,
    recipient_email: str | None,
    operation_deadline: float | None = None,
) -> str | None:
    _prepare_smtp_session(
        server,
        active,
        operation_deadline=operation_deadline,
    )
    _set_smtp_operation_timeout(server, operation_deadline)
    if recipient_email:
        refused = server.send_message(_build_test_message(active, recipient_email))
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return "Test email accepted by SMTP server."

    code, message = server.noop()
    return _format_smtp_reply(code, message)


def _build_test_message(
    active: ActiveSMTPSettings, recipient_email: str
) -> EmailMessage:
    message = EmailMessage()
    assert active.from_email is not None
    subject, html_body = _render_test_message_content(active)
    _set_message_header(
        message,
        "From",
        formataddr((active.from_name or "ThreatLens", active.from_email)),
    )
    _set_message_header(message, "To", recipient_email)
    _set_message_header(message, "Subject", subject)
    message.set_content(_html_to_plain_text(html_body))
    message.add_alternative(html_body, subtype="html")
    return message


def dispatch_smtp_notification(
    db: Session,
    *,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_kind: str = "live",
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
) -> SMTPDispatchResult:
    acquire_smtp_configuration_read_lock(db)
    instance = db.scalar(
        select(IntegrationInstance).where(
            IntegrationInstance.system_key == SMTP_SYSTEM_KEY
        )
    )
    if instance is None or not instance.enabled:
        return SMTPDispatchResult(status="skipped", reason="smtp_disabled")

    delivery_id = uuid.uuid4()
    dedupe_key = _smtp_delivery_dedupe_key(
        instance_id=instance.id,
        event_type=event_type,
        delivery_kind=delivery_kind,
        item_id=getattr(item, "id", None),
        feed_id=getattr(feed, "id", None),
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    )

    try:
        active = build_active_smtp_settings(instance)
    except SMTPSecretError as exc:
        skip_reason = _smtp_delivery_attempt_skip_reason(
            db,
            instance_id=instance.id,
            dedupe_key=dedupe_key,
            event_type=event_type,
            delivery_kind=delivery_kind,
            feed=feed,
            item=item,
            source_delivery_id=source_delivery_id,
            scope_key=scope_key,
        )
        if skip_reason is not None:
            return SMTPDispatchResult(status="skipped", reason=skip_reason)
        result = _notification_failure_result(
            started_at=time.perf_counter(),
            attempted_at=datetime.now(timezone.utc),
            delivery_id=delivery_id,
            recipient_count=0,
            accepted_count=0,
            error_code="secret_error",
            error=str(exc),
            server_message=None,
        )
        _record_smtp_delivery_audit(
            db,
            instance=instance,
            result=result,
            event_type=event_type,
            delivery_kind=delivery_kind,
            dedupe_key=dedupe_key,
            feed=feed,
            item=item,
            source_delivery_id=source_delivery_id,
            scope_key=scope_key,
        )
        _apply_smtp_delivery_result(instance, result)
        return SMTPDispatchResult(
            status="failed", reason="secret_error", delivery=result
        )

    if not _smtp_runtime_configured(active):
        return SMTPDispatchResult(status="skipped", reason="smtp_not_configured")
    if not _active_smtp_matches_event(active, event_type=event_type, feed=feed):
        return SMTPDispatchResult(status="skipped", reason="smtp_event_not_matched")
    skip_reason = _smtp_delivery_attempt_skip_reason(
        db,
        instance_id=instance.id,
        dedupe_key=dedupe_key,
        event_type=event_type,
        delivery_kind=delivery_kind,
        feed=feed,
        item=item,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    )
    if skip_reason is not None:
        return SMTPDispatchResult(status="skipped", reason=skip_reason)

    active = replace(
        active,
        timeout_seconds=_fenced_smtp_io_timeout_seconds(active.timeout_seconds),
    )
    result = send_smtp_notification(
        active,
        event_type=event_type,
        feed=feed,
        item=item,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
        delivery_id=delivery_id,
    )
    _record_smtp_delivery_audit(
        db,
        instance=instance,
        result=result,
        event_type=event_type,
        delivery_kind=delivery_kind,
        dedupe_key=dedupe_key,
        feed=feed,
        item=item,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    )
    _apply_smtp_delivery_result(instance, result)
    return SMTPDispatchResult(
        status="sent" if result.success else "failed",
        reason=None if result.success else result.error_code,
        delivery=result,
    )


def attempt_smtp_integration_delivery(
    db: Session,
    *,
    instance: IntegrationInstance,
    delivery_id: uuid.UUID,
    dedupe_key: str,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_kind: str = "live",
    source_delivery_id: uuid.UUID | None = None,
    scope_key: str | None = None,
    recipient_override: list[str] | None = None,
    lease_heartbeat: Callable[[int, ActiveSMTPSettings], None] | None = None,
    on_external_side_effect_possible: Callable[[], None] | None = None,
) -> SMTPDispatchResult:
    """Attempt one already-claimed generic delivery and preserve SMTP audit history."""
    started_at = time.perf_counter()
    attempted_at = datetime.now(timezone.utc)
    try:
        credential_source = get_smtp_credential_source(db, instance)
        active = build_active_smtp_settings(
            instance, credential_source=credential_source
        )
    except SQLAlchemyError as exc:
        raise SMTPDeliveryDatabasePreflightError(
            "SMTP credentials could not be loaded because the database preflight failed. The delivery will retry before sending any message."
        ) from exc
    except SMTPSecretError as exc:
        result = _notification_failure_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=delivery_id,
            recipient_count=0,
            accepted_count=0,
            error_code="secret_error",
            error=str(exc),
            server_message=None,
        )
    else:
        persisted_settings = active
        if not active.enabled:
            return SMTPDispatchResult(
                status="skipped", reason="smtp_integration_disabled"
            )
        if not _smtp_runtime_configured(active):
            result = _notification_failure_result(
                started_at=started_at,
                attempted_at=attempted_at,
                delivery_id=delivery_id,
                recipient_count=len(active.to_emails),
                accepted_count=0,
                error_code="not_configured",
                error="SMTP integration is enabled but is not fully configured.",
                server_message=None,
            )
        elif not _active_smtp_matches_event(active, event_type=event_type, feed=feed):
            return SMTPDispatchResult(status="skipped", reason="smtp_event_not_matched")
        else:
            if recipient_override is not None:
                recipients = _matching_configured_recipients(
                    active.to_emails, recipient_override
                )
                if not recipients:
                    return SMTPDispatchResult(
                        status="skipped", reason="smtp_replay_no_matching_recipients"
                    )
                active = replace(active, to_emails=recipients)
            if lease_heartbeat is not None:
                active = replace(
                    active,
                    timeout_seconds=_fenced_smtp_io_timeout_seconds(
                        active.timeout_seconds
                    ),
                )
            delivery_heartbeat = (
                persisted_smtp_settings_heartbeat(
                    lease_heartbeat,
                    persisted_settings=persisted_settings,
                )
                if lease_heartbeat is not None
                else None
            )
            result = send_smtp_notification(
                active,
                event_type=event_type,
                feed=feed,
                item=item,
                alert_context=alert_context,
                failed_webhook_context=failed_webhook_context,
                digest_context=digest_context,
                delivery_id=delivery_id,
                lease_heartbeat=delivery_heartbeat,
                on_external_side_effect_possible=on_external_side_effect_possible,
            )

    _record_smtp_delivery_audit(
        db,
        instance=instance,
        result=result,
        event_type=event_type,
        delivery_kind=delivery_kind,
        dedupe_key=dedupe_key,
        feed=feed,
        item=item,
        source_delivery_id=source_delivery_id,
        scope_key=scope_key,
    )
    _apply_smtp_delivery_result(instance, result)
    db.add(instance)
    return SMTPDispatchResult(
        status="sent" if result.success else "failed",
        reason=None if result.success else result.error_code,
        delivery=result,
    )


def smtp_notification_event_enabled(
    db: Session,
    *,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
) -> bool:
    """Return whether a runtime task should enqueue an SMTP delivery attempt."""
    instance = db.scalar(
        select(IntegrationInstance).where(
            IntegrationInstance.system_key == SMTP_SYSTEM_KEY
        )
    )
    if instance is None or not instance.enabled:
        return False
    try:
        active = build_active_smtp_settings(instance)
    except SMTPSecretError:
        saved = smtp_settings_response_from_model(instance)
        has_routable_configuration = bool(
            saved.host and saved.from_email and saved.to_emails
        )
        return has_routable_configuration and _smtp_config_matches_event(
            event_types=list(saved.event_types),
            feed_scope=saved.feed_scope,
            feed_ids=list(saved.feed_ids),
            event_type=event_type,
            feed=feed,
        )
    return _smtp_runtime_configured(active) and _active_smtp_matches_event(
        active, event_type=event_type, feed=feed
    )


def send_smtp_notification(
    active: ActiveSMTPSettings,
    *,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None = None,
    item: Item | SimpleNamespace | None = None,
    alert_context: AlertMatchContext | None = None,
    failed_webhook_context: FailedWebhookContext | None = None,
    digest_context: DailyDigestContext | None = None,
    delivery_id: uuid.UUID | None = None,
    lease_heartbeat: Callable[[int, ActiveSMTPSettings], None] | None = None,
    on_external_side_effect_possible: Callable[[], None] | None = None,
) -> SMTPNotificationResult:
    started_at = time.perf_counter()
    operation_deadline = started_at + max(
        SMTP_MIN_OPERATION_SECONDS, float(active.timeout_seconds)
    )
    attempted_at = datetime.now(timezone.utc)
    resolved_delivery_id = delivery_id or uuid.uuid4()
    _renew_smtp_operation_lease(lease_heartbeat, active)
    validation_error = _validate_notification_settings(active)
    if validation_error:
        return _notification_failure_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            recipient_count=len(active.to_emails),
            accepted_count=0,
            error_code="validation_error",
            error=validation_error,
            server_message=None,
        )

    try:
        message = _build_notification_message(
            active,
            event_type=event_type,
            feed=feed,
            item=item,
            alert_context=alert_context,
            failed_webhook_context=failed_webhook_context,
            digest_context=digest_context,
            delivery_id=resolved_delivery_id,
            attempted_at=attempted_at,
        )
    except TemplateRenderError as exc:
        return _notification_failure_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            recipient_count=len(active.to_emails),
            accepted_count=0,
            error_code="render_error",
            error=str(exc),
            server_message=None,
        )

    send_started = False
    try:
        with _enforce_smtp_operation_deadline(operation_deadline):
            _renew_smtp_operation_lease(lease_heartbeat, active)
            connection_settings = replace(
                active,
                timeout_seconds=_remaining_smtp_operation_seconds(operation_deadline),
            )
            server = _open_smtp(connection_settings)
            _install_smtp_deadline_hooks(server, operation_deadline)
            try:
                with _interrupt_smtp_transport_at_deadline(
                    server,
                    operation_deadline,
                ):
                    _prepare_smtp_session(
                        server,
                        active,
                        lease_heartbeat=lease_heartbeat,
                        operation_deadline=operation_deadline,
                    )
                    _renew_smtp_operation_lease(lease_heartbeat, active)
                    _set_smtp_operation_timeout(server, operation_deadline)
                    if on_external_side_effect_possible is not None:
                        on_external_side_effect_possible()
                    send_started = True
                    refused = server.send_message(message)
                    if refused:
                        refused_recipients = _matching_configured_recipients(
                            active.to_emails, list(refused)
                        )
                        refused_normalized = {
                            recipient.casefold() for recipient in refused_recipients
                        }
                        accepted_recipients = tuple(
                            recipient
                            for recipient in active.to_emails
                            if recipient.casefold() not in refused_normalized
                        )
                        return _notification_failure_result(
                            started_at=started_at,
                            attempted_at=attempted_at,
                            delivery_id=resolved_delivery_id,
                            recipient_count=len(active.to_emails),
                            accepted_count=len(accepted_recipients),
                            error_code="recipient_rejected",
                            error=f"SMTP server rejected {len(refused_recipients)} recipient(s).",
                            server_message=_recipients_refused_mapping_message(refused),
                            delivery_outcome="partial"
                            if accepted_recipients
                            else "rejected",
                            accepted_recipients=accepted_recipients,
                            refused_recipients=tuple(refused_recipients),
                        )
                    return SMTPNotificationResult(
                        success=True,
                        duration_ms=max(
                            0, int((time.perf_counter() - started_at) * 1000)
                        ),
                        recipient_count=len(active.to_emails),
                        accepted_count=len(active.to_emails),
                        error_code=None,
                        error=None,
                        server_message="Notification email accepted by SMTP server.",
                        attempted_at=attempted_at,
                        delivery_id=resolved_delivery_id,
                        delivery_outcome="accepted",
                        accepted_recipients=tuple(active.to_emails),
                    )
            finally:
                _close_smtp_transport(server)
    except SMTPStartTLSNotSupportedError as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="starttls_not_supported",
            error="SMTP server does not support the required STARTTLS capability.",
            server_message=str(exc) or None,
        )
    except smtplib.SMTPNotSupportedError as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="smtp_capability_unsupported",
            error="SMTP server does not support a required capability.",
            server_message=str(exc) or None,
        )
    except smtplib.SMTPAuthenticationError as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code=_smtp_response_error_code(
                exc.smtp_code,
                permanent_error_code="auth_error",
            ),
            error="SMTP authentication failed.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPRecipientsRefused as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code=_recipients_refused_error_code(exc),
            error="SMTP server rejected all recipients.",
            server_message=_recipients_refused_message(exc),
            delivery_outcome="rejected",
            refused_recipients=tuple(
                _matching_configured_recipients(active.to_emails, list(exc.recipients))
            ),
        )
    except smtplib.SMTPSenderRefused as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code=_smtp_response_error_code(
                exc.smtp_code,
                permanent_error_code="sender_rejected",
            ),
            error="SMTP server rejected the configured sender.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPConnectError as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="connect_error",
            error="SMTP server refused the connection.",
            server_message=_smtp_response_message(exc),
        )
    except smtplib.SMTPServerDisconnected as exc:
        if _smtp_operation_deadline_expired(operation_deadline):
            return _notification_timeout_result(
                started_at=started_at,
                attempted_at=attempted_at,
                delivery_id=resolved_delivery_id,
                active=active,
                send_started=send_started,
            )
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="connection_closed",
            error="SMTP server closed the connection unexpectedly.",
            server_message=str(exc) or None,
            delivery_outcome="unknown" if send_started else "not_attempted",
            unknown_recipients=tuple(active.to_emails) if send_started else (),
        )
    except smtplib.SMTPDataError as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code=_smtp_response_error_code(exc.smtp_code),
            error="SMTP server rejected the message body.",
            server_message=_smtp_response_message(exc),
            delivery_outcome="rejected",
            refused_recipients=tuple(active.to_emails),
        )
    except smtplib.SMTPResponseException as exc:
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code=_smtp_response_error_code(exc.smtp_code),
            error="SMTP server returned an error.",
            server_message=_smtp_response_message(exc),
        )
    except (TimeoutError, socket.timeout):
        return _notification_timeout_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            send_started=send_started,
        )
    except ssl.SSLError as exc:
        if _smtp_operation_deadline_expired(operation_deadline):
            return _notification_timeout_result(
                started_at=started_at,
                attempted_at=attempted_at,
                delivery_id=resolved_delivery_id,
                active=active,
                send_started=send_started,
            )
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="tls_error",
            error="SMTP TLS negotiation failed.",
            server_message=str(exc),
            delivery_outcome="unknown" if send_started else "not_attempted",
            unknown_recipients=tuple(active.to_emails) if send_started else (),
        )
    except OSError as exc:
        if _smtp_operation_deadline_expired(operation_deadline):
            return _notification_timeout_result(
                started_at=started_at,
                attempted_at=attempted_at,
                delivery_id=resolved_delivery_id,
                active=active,
                send_started=send_started,
            )
        return _notification_smtp_exception_result(
            started_at=started_at,
            attempted_at=attempted_at,
            delivery_id=resolved_delivery_id,
            active=active,
            error_code="connection_error",
            error="SMTP connection failed.",
            server_message=str(exc),
            delivery_outcome="unknown" if send_started else "not_attempted",
            unknown_recipients=tuple(active.to_emails) if send_started else (),
        )


def _close_smtp_transport(server: smtplib.SMTP) -> None:
    try:
        server.close()
    except (AttributeError, OSError):
        return


def _build_notification_message(
    active: ActiveSMTPSettings,
    *,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None,
    item: Item | SimpleNamespace | None,
    alert_context: AlertMatchContext | None,
    failed_webhook_context: FailedWebhookContext | None,
    digest_context: DailyDigestContext | None,
    delivery_id: uuid.UUID,
    attempted_at: datetime,
) -> EmailMessage:
    assert active.from_email is not None
    system_user = SimpleNamespace(
        id=active.id,
        email=active.from_email,
        is_active=True,
        is_approved=True,
        role="admin",
    )
    subject = render_notification_template_text(
        active.subject_template,
        user=system_user,
        feed=feed,
        item=item,
        event_type=event_type,
        triggered_at=attempted_at,
        delivery_id=delivery_id,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    html_body = render_notification_template_text(
        active.html_template,
        user=system_user,
        feed=feed,
        item=item,
        event_type=event_type,
        triggered_at=attempted_at,
        delivery_id=delivery_id,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )

    message = EmailMessage()
    _set_message_header(
        message,
        "From",
        formataddr((active.from_name or "ThreatLens", active.from_email)),
    )
    _set_message_header(message, "To", ", ".join(active.to_emails))
    _set_message_header(message, "Subject", subject)
    _set_message_header(message, "X-ThreatLens-Delivery-ID", str(delivery_id))
    _set_message_header(message, "X-ThreatLens-Event-Type", event_type)
    message.set_content(_html_to_plain_text(html_body))
    message.add_alternative(html_body, subtype="html")
    return message


def _set_message_header(message: EmailMessage, name: str, value: str) -> None:
    try:
        message[name] = value
    except ValueError as exc:
        raise TemplateRenderError(
            f"SMTP message header {name} is invalid: {exc}"
        ) from exc


def _validate_test_settings(
    active: ActiveSMTPSettings, *, recipient_email: str | None
) -> str | None:
    if not active.host:
        return "SMTP host is required before testing."
    if active.username and not active.password:
        return "SMTP password is required when a username is configured."
    if recipient_email and not active.from_email:
        return "Sender email is required before sending a test email."
    if recipient_email:
        try:
            _build_test_message(active, recipient_email)
        except TemplateRenderError as exc:
            return str(exc)
    return None


def _validate_notification_settings(active: ActiveSMTPSettings) -> str | None:
    if not active.enabled:
        return "SMTP is disabled."
    if not active.host:
        return "SMTP host is required before sending notifications."
    if active.username and not active.password:
        return "SMTP password is required when a username is configured."
    if not active.from_email:
        return "Sender email is required before sending notifications."
    if not active.to_emails:
        return "At least one recipient email is required before sending notifications."
    return None


def _smtp_runtime_configured(active: ActiveSMTPSettings) -> bool:
    return bool(
        active.enabled and active.host and active.from_email and active.to_emails
    )


def _active_smtp_matches_event(
    active: ActiveSMTPSettings,
    *,
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None,
) -> bool:
    return _smtp_config_matches_event(
        event_types=active.event_types,
        feed_scope=active.feed_scope,
        feed_ids=active.feed_ids,
        event_type=event_type,
        feed=feed,
    )


def _smtp_config_matches_event(
    *,
    event_types: list[str],
    feed_scope: str,
    feed_ids: list[uuid.UUID],
    event_type: NotificationEventType,
    feed: Feed | SimpleNamespace | None,
) -> bool:
    if event_type not in event_types:
        return False
    if feed_scope == "all":
        return True
    if event_type in {"daily_digest", "report_ready"}:
        return True
    feed_id = getattr(feed, "id", None)
    return feed_id is not None and feed_id in feed_ids


def _render_test_message_content(active: ActiveSMTPSettings) -> tuple[str, str]:
    event_type = active.event_types[0] if active.event_types else "rss_item_new"
    triggered_at = datetime.now(timezone.utc)
    delivery_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        email="analyst@example.com",
    )
    feed = SimpleNamespace(
        id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
        name="Example Threat Feed",
        url="https://feeds.example.com/rss.xml",
        site_url="https://feeds.example.com",
        error_count=3,
        last_error="HTTP 503 from upstream feed",
        last_fetch_at=triggered_at,
        last_success_at=triggered_at,
    )
    item = SimpleNamespace(
        id=uuid.UUID("00000000-0000-4000-8000-000000000003"),
        title="Example intrusion activity observed",
        url="https://example.com/threat-report",
        canonical_url="https://example.com/threat-report",
        summary="ThreatLens test email using the configured SMTP notification template.",
        status="new",
        published_at=triggered_at,
        first_seen_at=triggered_at,
    )
    alert_context = (
        AlertMatchContext(
            count=1,
            primary_name="Threat activity",
            names=["Threat activity"],
            categories=["monitoring"],
            matched_keywords=["intrusion", "malware"],
        )
        if event_type == "alert_match"
        else None
    )
    failed_webhook_context = (
        FailedWebhookContext(
            id=uuid.UUID("00000000-0000-4000-8000-000000000004"),
            name="Example webhook",
            event_type="rss_item_new",
            status_code=500,
            error="HTTP 500",
            attempted_at=triggered_at,
        )
        if event_type == "webhook_failed"
        else None
    )
    digest_context = (
        DailyDigestContext(
            window_start=triggered_at - timedelta(hours=24),
            window_end=triggered_at,
            total_items=2,
            total_feeds=1,
            feed_names=["Example Threat Feed"],
            top_titles=[
                "Example intrusion activity observed",
                "Example vulnerable product advisory",
            ],
            brief_id=uuid.UUID("00000000-0000-4000-8000-000000000005"),
            brief_date=triggered_at.date().isoformat(),
            generated_at=triggered_at,
            title="Example AI Daily Brief",
            brief_text="Identity abuse and exposed services are the highest-priority developments.",
            key_points=["Review identity telemetry", "Track exposed edge services"],
            recommended_actions=["Validate MFA coverage", "Confirm edge patch status"],
        )
        if event_type in {"daily_digest", "report_ready"}
        else None
    )
    subject = render_notification_template_text(
        active.subject_template,
        user=user,
        feed=feed,
        item=item,
        event_type=event_type,
        triggered_at=triggered_at,
        delivery_id=delivery_id,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    html_body = render_notification_template_text(
        active.html_template,
        user=user,
        feed=feed,
        item=item,
        event_type=event_type,
        triggered_at=triggered_at,
        delivery_id=delivery_id,
        alert_context=alert_context,
        failed_webhook_context=failed_webhook_context,
        digest_context=digest_context,
    )
    return subject, html_body


def _html_to_plain_text(html_body: str) -> str:
    text = HTML_TAG_PATTERN.sub(" ", html_body)
    text = unescape(text)
    return " ".join(text.split()) or "ThreatLens SMTP test email."


def _matching_configured_recipients(
    configured: list[str], requested: list[str]
) -> list[str]:
    requested_normalized = {
        recipient.strip().casefold()
        for recipient in requested
        if isinstance(recipient, str) and recipient.strip()
    }
    return [
        recipient
        for recipient in configured
        if recipient.casefold() in requested_normalized
    ]
