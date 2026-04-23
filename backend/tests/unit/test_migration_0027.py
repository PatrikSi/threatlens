from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from app.core.config import get_settings


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0027_feed_url_secret_storage.py"
    spec = spec_from_file_location("migration_0027_feed_url_secret_storage", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_migration_feed_url_digest_keeps_authenticated_variants_distinct():
    migration = _load_migration_module()

    assert migration._feed_url_digest("https://example.com/path/feed.xml?token=alpha") != migration._feed_url_digest(
        "https://example.com/path/feed.xml?token=beta"
    )
    assert migration._feed_url_digest("https://alice:secret@example.com/path/feed.xml") != migration._feed_url_digest(
        "https://bob:secret@example.com/path/feed.xml"
    )


def test_migration_can_round_trip_feed_url_ciphertext(monkeypatch: pytest.MonkeyPatch):
    plaintext_url = "https://alice:secret@example.com/path/feed.xml?token=alpha"
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "migration-storage-secret-" + "x" * 32)

    migration = _load_migration_module()
    ciphertext = migration._encrypt_text(plaintext_url)

    assert ciphertext.startswith("enc:v1:")
    assert migration._decrypt_maybe(ciphertext) == plaintext_url
    assert migration._decrypt_maybe(plaintext_url) == plaintext_url
