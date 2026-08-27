import hashlib

import pytest

from app.core.config import get_settings
from app.services.secret_storage import (
    decrypt_json,
    decrypt_text,
    decrypt_text_with_rotation,
    encrypt_json,
    encrypt_text,
    keyed_hexdigest,
    keyed_hexdigest_candidates,
    stored_keyed_digest_key_id,
    versioned_keyed_hexdigest,
    versioned_keyed_hexdigest_candidates,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_secret_storage_uses_dedicated_data_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-for-signing-only-" + "x" * 24)
    monkeypatch.setenv(
        "APP_DATA_ENCRYPTION_KEY", "dedicated-storage-secret-" + "y" * 24
    )

    encrypted_text = encrypt_text("hello")
    encrypted_json = encrypt_json({"status": "ok"})

    assert encrypted_text != "hello"
    assert decrypt_text(encrypted_text) == "hello"
    assert decrypt_json(encrypted_json) == {"status": "ok"}


def test_secret_storage_development_fallback_is_independent_of_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("JWT_SECRET", "legacy-jwt-secret-" + "x" * 32)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)

    ciphertext = encrypt_text("keep-me-readable")

    monkeypatch.setenv("JWT_SECRET", "rotated-jwt-secret-" + "z" * 31)
    get_settings.cache_clear()

    assert decrypt_text(ciphertext) == "keep-me-readable"


def test_secret_storage_can_decrypt_ciphertext_with_previous_key_ring(
    monkeypatch: pytest.MonkeyPatch,
):
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


def test_keyed_hexdigest_candidates_preserve_rotation_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
):
    previous_key = "previous-storage-secret-" + "p" * 32
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", previous_key)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    previous_digest = keyed_hexdigest("recovery-code", purpose="mfa")

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "current-storage-secret-" + "c" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", previous_key)
    get_settings.cache_clear()
    candidates = keyed_hexdigest_candidates("recovery-code", purpose="mfa")

    assert len(candidates) == 2
    assert previous_digest in candidates


def test_versioned_keyed_digest_identifies_and_migrates_rotation_dependencies(
    monkeypatch: pytest.MonkeyPatch,
):
    previous_key = "previous-versioned-key-" + "p" * 32
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", previous_key)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    previous_versioned = versioned_keyed_hexdigest(
        "recovery-code", purpose="mfa"
    )
    previous_legacy = keyed_hexdigest("recovery-code", purpose="mfa")

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "current-versioned-key-" + "c" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", previous_key)
    get_settings.cache_clear()
    candidates = versioned_keyed_hexdigest_candidates(
        "recovery-code", purpose="mfa"
    )
    current = versioned_keyed_hexdigest("recovery-code", purpose="mfa")

    assert current is not None and current.startswith("hmac:v1:")
    assert stored_keyed_digest_key_id(current) not in {None, "invalid"}
    assert previous_versioned in candidates
    assert previous_legacy in candidates


def test_decrypt_text_reports_previous_key_rotation(monkeypatch: pytest.MonkeyPatch):
    previous_key = "previous-encryption-key-" + "p" * 32
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", previous_key)
    monkeypatch.delenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", raising=False)
    get_settings.cache_clear()
    previous_ciphertext = encrypt_text("rotate-me")

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "current-encryption-key-" + "c" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", previous_key)
    get_settings.cache_clear()

    assert decrypt_text_with_rotation(previous_ciphertext) == ("rotate-me", True)
    assert decrypt_text_with_rotation(encrypt_text("current")) == ("current", False)
