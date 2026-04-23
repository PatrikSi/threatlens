from __future__ import annotations

UNREADABLE_FEED_URL_ERROR = (
    "Stored feed URL cannot be decrypted. Restore APP_DATA_ENCRYPTION_KEY or "
    "APP_DATA_ENCRYPTION_PREVIOUS_KEYS, or recreate the feed."
)


def encrypt_feed_url(value: str) -> str:
    from app.services.secret_storage import encrypt_text

    encrypted = encrypt_text(value)
    if encrypted is None:  # pragma: no cover - defensive only
        raise ValueError("Feed URL cannot be empty")
    return encrypted


def decrypt_feed_url(value: str | None) -> str | None:
    from app.services.secret_storage import decrypt_text

    return decrypt_text(value)


def try_decrypt_feed_url(value: str | None) -> tuple[str | None, str | None]:
    try:
        return decrypt_feed_url(value), None
    except ValueError:
        return None, UNREADABLE_FEED_URL_ERROR


def feed_url_digest(value: str | None) -> str | None:
    plaintext = decrypt_feed_url(value)
    if plaintext is None:
        return None
    from app.services.secret_storage import keyed_hexdigest

    return keyed_hexdigest(plaintext, purpose="feed-url-digest")
