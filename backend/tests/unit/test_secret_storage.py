import hashlib

import pytest

from app.core.config import get_settings
from app.services.secret_storage import decrypt_json, decrypt_text, encrypt_json, encrypt_text, keyed_hexdigest


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_secret_storage_uses_dedicated_data_encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-for-signing-only-" + "x" * 24)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "dedicated-storage-secret-" + "y" * 24)

    encrypted_text = encrypt_text("hello")
    encrypted_json = encrypt_json({"status": "ok"})

    assert encrypted_text != "hello"
    assert decrypt_text(encrypted_text) == "hello"
    assert decrypt_json(encrypted_json) == {"status": "ok"}


def test_secret_storage_does_not_fall_back_to_jwt_secret_for_encryption(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "legacy-jwt-secret-" + "x" * 32)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)

    ciphertext = encrypt_text("keep-me-readable")

    monkeypatch.setenv("JWT_SECRET", "rotated-jwt-secret-" + "z" * 31)
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        decrypt_text(ciphertext)


def test_secret_storage_can_decrypt_ciphertext_with_previous_key_ring(monkeypatch: pytest.MonkeyPatch):
    original_storage_secret = "legacy-storage-secret-" + "x" * 32
    monkeypatch.setenv("JWT_SECRET", "legacy-jwt-secret-" + "j" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", original_storage_secret)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)

    legacy_ciphertext = encrypt_text("carry-forward")

    monkeypatch.setenv("JWT_SECRET", "rotated-jwt-secret-" + "z" * 31)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "new-storage-secret-" + "y" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", original_storage_secret)
    get_settings.cache_clear()

    assert decrypt_text(legacy_ciphertext) == "carry-forward"


def test_keyed_hexdigest_uses_application_secret(monkeypatch: pytest.MonkeyPatch):
    plaintext = "https://alice:secret@example.com/path/feed.xml?token=alpha"
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "digest-storage-secret-" + "x" * 32)

    digest = keyed_hexdigest(plaintext, purpose="feed-url-digest")

    assert digest is not None
    assert digest == keyed_hexdigest(plaintext, purpose="feed-url-digest")
    assert digest != keyed_hexdigest(plaintext, purpose="other-purpose")
    assert digest != hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
