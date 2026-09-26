"""Resolve live credentials only at the fenced outbound boundary."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.webhook_credential import WebhookCredentialProfile
from app.schemas.webhook_automation import (
    WebhookCredentialResponse,
    WebhookCredentialWrite,
)
from app.services.secret_storage import decrypt_text, encrypt_text


def credential_response(profile: WebhookCredentialProfile) -> WebhookCredentialResponse:
    return WebhookCredentialResponse(
        id=profile.id,
        name=profile.name,
        enabled=profile.enabled,
        auth_type=profile.auth_type,
        header_name=profile.header_name,
        auth_configured=bool(profile.auth_secret_encrypted),
        signing_configured=bool(profile.signing_secret_encrypted),
        revision=profile.revision,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def update_credential(
    profile: WebhookCredentialProfile, payload: WebhookCredentialWrite
) -> None:
    profile.name = payload.name
    profile.enabled = payload.enabled
    profile.auth_type = payload.auth_type
    profile.header_name = payload.header_name if payload.auth_type == "header" else None
    if payload.auth_secret is not None or payload.clear_auth_secret:
        profile.auth_secret_encrypted = encrypt_text(payload.auth_secret)
    if payload.signing_secret is not None or payload.clear_signing_secret:
        profile.signing_secret_encrypted = encrypt_text(payload.signing_secret)
    if (
        profile.enabled
        and profile.auth_type != "none"
        and not profile.auth_secret_encrypted
    ):
        raise HTTPException(
            422, "Enabled authenticated profiles require an authentication secret"
        )


def load_credential(
    db: Session,
    *,
    profile_id: uuid.UUID,
    user_id: uuid.UUID,
    lock: bool = False,
    require_enabled: bool = True,
) -> WebhookCredentialProfile:
    query = select(WebhookCredentialProfile).where(
        WebhookCredentialProfile.id == profile_id,
        WebhookCredentialProfile.user_id == user_id,
    )
    if lock:
        query = query.with_for_update(read=True).execution_options(
            populate_existing=True
        )
    profile = db.scalar(query)
    if profile is None or (require_enabled and not profile.enabled):
        raise ValueError("Webhook credential profile is unavailable or disabled")
    return profile


def signed_headers(
    *,
    secret: str,
    body: bytes,
    event_id: str,
    attempt_id: str,
    timestamp: int | None = None,
    key_revision: int = 1,
) -> dict[str, str]:
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    canonical = (
        b"v1\n"
        + stamp.encode("ascii")
        + b"\n"
        + event_id.encode("ascii")
        + b"\n"
        + attempt_id.encode("ascii")
        + b"\n"
        + body
    )
    digest = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return {
        "X-ThreatLens-Signature": f"v1={digest}",
        "X-ThreatLens-Timestamp": stamp,
        "X-ThreatLens-Event-ID": event_id,
        "X-ThreatLens-Attempt-ID": attempt_id,
        "X-ThreatLens-Key-Revision": str(key_revision),
    }


def credential_request_callback(
    db: Session,
    *,
    profile_id: uuid.UUID | None = None,
    webhook_id: uuid.UUID | None = None,
    user_id: uuid.UUID,
    event_id: str,
    attempt_id: str,
):
    def prepare(request) -> None:
        from app.models.notification_webhook import NotificationWebhook
        from app.services.webhook_delivery_eligibility import (
            WebhookDeliveryIneligibleError,
        )

        selected = profile_id
        if webhook_id is not None:
            webhook = db.get(NotificationWebhook, webhook_id)
            selected = webhook.credential_profile_id if webhook is not None else None
        if selected is None:
            return
        try:
            profile = load_credential(
                db, profile_id=selected, user_id=user_id, lock=True
            )
            if profile.auth_type != "none":
                secret = decrypt_text(profile.auth_secret_encrypted)
                if not secret:
                    raise ValueError("Webhook authentication secret is unavailable")
                key = (
                    "Authorization"
                    if profile.auth_type == "bearer"
                    else profile.header_name
                )
                if not key:
                    raise ValueError("Webhook authentication header is unavailable")
                request.headers[key] = (
                    f"Bearer {secret}" if profile.auth_type == "bearer" else secret
                )
            if profile.signing_secret_encrypted:
                secret = decrypt_text(profile.signing_secret_encrypted)
                if not secret:
                    raise ValueError("Webhook signing secret is unavailable")
                request.headers.update(
                    signed_headers(
                        secret=secret,
                        body=request.read(),
                        event_id=event_id,
                        attempt_id=attempt_id,
                        key_revision=profile.revision,
                    )
                )
        except ValueError as exc:
            raise WebhookDeliveryIneligibleError(
                "webhook_credential_unavailable",
                "Webhook credential profile or its secret is unavailable",
            ) from exc

    return prepare
