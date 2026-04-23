from __future__ import annotations


def encrypt_feed_url(value: str) -> str:
    from app.services.secret_storage import encrypt_text

    encrypted = encrypt_text(value)
    if encrypted is None:  # pragma: no cover - defensive only
        raise ValueError("Feed URL cannot be empty")
    return encrypted


def decrypt_feed_url(value: str | None) -> str | None:
    from app.services.secret_storage import decrypt_text

    return decrypt_text(value)


def feed_url_digest(value: str | None) -> str | None:
    plaintext = decrypt_feed_url(value)
    if plaintext is None:
        return None
    from app.services.secret_storage import keyed_hexdigest

    return keyed_hexdigest(plaintext, purpose="feed-url-digest")
