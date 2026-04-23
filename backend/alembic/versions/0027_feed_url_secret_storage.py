"""encrypt feed URLs at rest and preserve unique lookup semantics

Revision ID: 0027_feed_url_secret
Revises: 0026_notification_retry_nb
Create Date: 2026-04-23
"""

from __future__ import annotations

import base64
import hashlib

from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


revision = "0027_feed_url_secret"
down_revision = "0026_notification_retry_nb"
branch_labels = None
depends_on = None

_ENCRYPTED_TEXT_PREFIX = "enc:v1:"


def upgrade() -> None:
    op.add_column("feeds", sa.Column("url_digest", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    metadata = sa.MetaData()
    feeds = sa.Table("feeds", metadata, autoload_with=bind)
    rows = bind.execute(sa.select(feeds.c.id, feeds.c.url).order_by(feeds.c.created_at.asc(), feeds.c.id.asc())).mappings().all()

    for row in rows:
        plaintext_url = _decrypt_maybe(row["url"]) or ""
        bind.execute(
            sa.update(feeds)
            .where(feeds.c.id == row["id"])
            .values(
                url=_encrypt_text(plaintext_url),
                url_digest=_feed_url_digest(plaintext_url),
            )
        )

    op.alter_column("feeds", "url_digest", nullable=False)
    op.drop_constraint("uq_feeds_url", "feeds", type_="unique")
    op.create_unique_constraint("uq_feeds_url_digest", "feeds", ["url_digest"])


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    feeds = sa.Table("feeds", metadata, autoload_with=bind)
    rows = bind.execute(sa.select(feeds.c.id, feeds.c.url).order_by(feeds.c.created_at.asc(), feeds.c.id.asc())).mappings().all()

    for row in rows:
        bind.execute(
            sa.update(feeds)
            .where(feeds.c.id == row["id"])
            .values(url=_decrypt_maybe(row["url"]) or "")
        )

    op.drop_constraint("uq_feeds_url_digest", "feeds", type_="unique")
    op.drop_column("feeds", "url_digest")
    op.create_unique_constraint("uq_feeds_url", "feeds", ["url"])


def _feed_url_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    settings = get_settings()
    secret = settings.app_data_encryption_key
    if not secret:
        raise ValueError("app_data_encryption_key must be configured before encrypting stored data")
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


def _build_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)
