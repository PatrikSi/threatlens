from __future__ import annotations

import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from app.core.config import get_settings


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0029_webhook_secret_backfill.py"
    spec = spec_from_file_location("migration_0029_webhook_secret_backfill", migration_path)
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


def test_migration_feed_url_digest_uses_keyed_hash(monkeypatch: pytest.MonkeyPatch):
    plaintext_url = "https://alice:secret@example.com/path/feed.xml?token=alpha"
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "migration-storage-secret-" + "x" * 32)

    migration = _load_migration_module()

    digest = migration._feed_url_digest(plaintext_url)

    assert digest == migration._feed_url_digest(plaintext_url)
    assert digest != hashlib.sha256(plaintext_url.encode("utf-8")).hexdigest()


def test_migration_encrypts_legacy_notification_webhook_fields(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "migration-storage-secret-" + "x" * 32)
    migration = _load_migration_module()

    values = migration._notification_webhook_update_values(
        {
            "url_template": "https://hooks.example.com/notify",
            "query_params_json": [{"key": "token", "value": "alpha"}],
            "headers_json": [{"key": "Authorization", "value": "Bearer secret"}],
            "body_fields_json": [{"key": "title", "value": "ThreatLens"}],
            "body_template": '{"title":"ThreatLens"}',
        }
    )

    assert migration._decrypt_maybe(values["url_template"]) == "https://hooks.example.com/notify"
    assert json.loads(migration._decrypt_maybe(values["query_params_json"][migration._ENCRYPTED_JSON_KEY])) == [
        {"key": "token", "value": "alpha"}
    ]
    assert json.loads(migration._decrypt_maybe(values["headers_json"][migration._ENCRYPTED_JSON_KEY])) == [
        {"key": "Authorization", "value": "Bearer secret"}
    ]
    assert json.loads(migration._decrypt_maybe(values["body_fields_json"][migration._ENCRYPTED_JSON_KEY])) == [
        {"key": "title", "value": "ThreatLens"}
    ]
    assert migration._decrypt_maybe(values["body_template"]) == '{"title":"ThreatLens"}'
