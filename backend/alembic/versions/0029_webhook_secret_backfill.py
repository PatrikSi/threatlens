"""backfill webhook secret storage and keyed feed digests

Revision ID: 0029_webhook_secret_backfill
Revises: 0028_item_ioc_extract
Create Date: 2026-04-23
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


revision = "0029_webhook_secret_backfill"
down_revision = "0028_item_ioc_extract"
branch_labels = None
depends_on = None

_ENCRYPTED_JSON_KEY = "_threatlens_encrypted"
_ENCRYPTED_TEXT_PREFIX = "enc:v1:"
_FEED_URL_DIGEST_PURPOSE = "feed-url-digest"


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    feeds = sa.Table("feeds", metadata, autoload_with=bind)
    webhooks = sa.Table("notification_webhooks", metadata, autoload_with=bind)
    deliveries = sa.Table("notification_webhook_deliveries", metadata, autoload_with=bind)

    feed_rows = bind.execute(sa.select(feeds.c.id, feeds.c.url).order_by(feeds.c.created_at.asc(), feeds.c.id.asc())).mappings().all()
    for row in feed_rows:
        plaintext_url = _decrypt_maybe(row["url"]) or ""
        values = {"url_digest": _feed_url_digest(plaintext_url)}
        encrypted_url = _encrypt_text_if_legacy(row["url"])
        if encrypted_url != row["url"]:
            values["url"] = encrypted_url
        bind.execute(sa.update(feeds).where(feeds.c.id == row["id"]).values(**values))

    webhook_rows = bind.execute(
        sa.select(
            webhooks.c.id,
            webhooks.c.url_template,
            webhooks.c.query_params_json,
            webhooks.c.headers_json,
            webhooks.c.body_fields_json,
            webhooks.c.body_template,
        ).order_by(webhooks.c.created_at.asc(), webhooks.c.id.asc())
    ).mappings().all()
    for row in webhook_rows:
        values = _notification_webhook_update_values(row)
        if values:
            bind.execute(sa.update(webhooks).where(webhooks.c.id == row["id"]).values(**values))

    delivery_rows = bind.execute(
        sa.select(
            deliveries.c.id,
            deliveries.c.rendered_url,
            deliveries.c.rendered_headers_json,
            deliveries.c.rendered_query_params_json,
            deliveries.c.rendered_body,
            deliveries.c.response_body_preview,
        ).order_by(deliveries.c.attempted_at.asc(), deliveries.c.id.asc())
    ).mappings().all()
    for row in delivery_rows:
        values = _notification_delivery_update_values(row)
        if values:
            bind.execute(sa.update(deliveries).where(deliveries.c.id == row["id"]).values(**values))


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    feeds = sa.Table("feeds", metadata, autoload_with=bind)

    rows = bind.execute(sa.select(feeds.c.id, feeds.c.url).order_by(feeds.c.created_at.asc(), feeds.c.id.asc())).mappings().all()
    for row in rows:
        plaintext_url = _decrypt_maybe(row["url"]) or ""
        bind.execute(
            sa.update(feeds)
            .where(feeds.c.id == row["id"])
            .values(url_digest=hashlib.sha256(plaintext_url.encode("utf-8")).hexdigest())
        )


def _notification_webhook_update_values(row) -> dict[str, object]:
    values: dict[str, object] = {}

    updated = _encrypt_text_if_legacy(row["url_template"])
    if updated != row["url_template"]:
        values["url_template"] = updated

    updated = _encrypt_json_if_legacy(row["query_params_json"])
    if updated != row["query_params_json"]:
        values["query_params_json"] = updated

    updated = _encrypt_json_if_legacy(row["headers_json"])
    if updated != row["headers_json"]:
        values["headers_json"] = updated

    updated = _encrypt_json_if_legacy(row["body_fields_json"])
    if updated != row["body_fields_json"]:
        values["body_fields_json"] = updated

    updated = _encrypt_text_if_legacy(row["body_template"])
    if updated != row["body_template"]:
        values["body_template"] = updated

    return values


def _notification_delivery_update_values(row) -> dict[str, object]:
    values: dict[str, object] = {}

    updated = _encrypt_text_if_legacy(row["rendered_url"])
    if updated != row["rendered_url"]:
        values["rendered_url"] = updated

    updated = _encrypt_json_if_legacy(row["rendered_headers_json"])
    if updated != row["rendered_headers_json"]:
        values["rendered_headers_json"] = updated

    updated = _encrypt_json_if_legacy(row["rendered_query_params_json"])
    if updated != row["rendered_query_params_json"]:
        values["rendered_query_params_json"] = updated

    updated = _encrypt_text_if_legacy(row["rendered_body"])
    if updated != row["rendered_body"]:
        values["rendered_body"] = updated

    updated = _encrypt_text_if_legacy(row["response_body_preview"])
    if updated != row["response_body_preview"]:
        values["response_body_preview"] = updated

    return values


def _feed_url_digest(value: str) -> str:
    payload = f"{_FEED_URL_DIGEST_PURPOSE}\x00{value}".encode("utf-8")
    return hmac.new(_hashing_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _encrypt_json_if_legacy(value):
    if value is None or _is_encrypted_json(value):
        return value
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return {_ENCRYPTED_JSON_KEY: _encrypt_text(payload)}


def _encrypt_text_if_legacy(value: str | None) -> str | None:
    if value is None or value.startswith(_ENCRYPTED_TEXT_PREFIX):
        return value
    return _encrypt_text(value)


def _is_encrypted_json(value) -> bool:
    return isinstance(value, dict) and set(value.keys()) == {_ENCRYPTED_JSON_KEY} and isinstance(value[_ENCRYPTED_JSON_KEY], str)


def _encrypt_text(value: str) -> str:
    token = _encryption_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTED_TEXT_PREFIX}{token}"


def _decrypt_maybe(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(_ENCRYPTED_TEXT_PREFIX):
        return value
    token = value[len(_ENCRYPTED_TEXT_PREFIX) :]
    for fernet in _decryption_fernets():
        try:
            return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            continue
    raise ValueError("Unable to decrypt stored data")


def _encryption_fernet() -> Fernet:
    secret = _hashing_secret()
    return _build_fernet(secret)


def _decryption_fernets() -> list[Fernet]:
    settings = get_settings()
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(secret: str | None) -> None:
        normalized = (secret or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    _append(settings.app_data_encryption_key)
    for previous_key in settings.app_data_encryption_previous_keys:
        _append(previous_key)

    return [_build_fernet(secret) for secret in candidates]


def _hashing_secret() -> str:
    settings = get_settings()
    secret = (settings.app_data_encryption_key or "").strip()
    if not secret:
        raise ValueError("app_data_encryption_key must be configured before hashing stored data")
    return secret


def _build_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)
