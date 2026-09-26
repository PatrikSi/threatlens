from sqlalchemy import event

from app.core.config import get_settings
from app.models.webhook_credential import WebhookCredentialProfile
from app.services import encrypted_data_inventory
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.secret_storage import encrypt_text


def add_profile(db, owner_id, **fields):
    row = WebhookCredentialProfile(user_id=owner_id, name="Inventory fixture", **fields)
    db.add(row)
    db.commit()
    return row


def test_webhook_secret_inventory_covers_rotation_fallback_and_lost_keys(db_session, seed_users, monkeypatch):
    old_key = "webhook-inventory-old-" + "a" * 40
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", old_key)
    get_settings.cache_clear()
    add_profile(db_session, seed_users["analyst"].id, auth_type="bearer",
                auth_secret_encrypted=encrypt_text("synthetic-bearer"),
                signing_secret_encrypted=encrypt_text("synthetic-signing-key-" + "b" * 32))
    initial = scan_encrypted_data_inventory(db_session)
    assert initial.webhook_credential_secrets.total_records == 1
    assert initial.webhook_credential_secrets.encrypted_fields == 2
    assert initial.webhook_credential_secrets.unreadable_fields == 0
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "webhook-inventory-new-" + "c" * 40)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", old_key)
    get_settings.cache_clear()
    assert scan_encrypted_data_inventory(db_session).webhook_credential_secrets.unreadable_fields == 0
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", "")
    get_settings.cache_clear()
    missing_key = scan_encrypted_data_inventory(db_session)
    assert missing_key.webhook_credential_secrets.unreadable_records == 1
    assert missing_key.webhook_credential_secrets.unreadable_fields == 2
    assert missing_key.summary.unreadable_fields >= 2
    assert missing_key.ok is False
    assert missing_key.status == "critical"


def test_disabled_and_unencrypted_profile_secrets_are_not_omitted(db_session, seed_users):
    add_profile(db_session, seed_users["analyst"].id, enabled=False,
                auth_secret_encrypted="unexpected-plaintext", signing_secret_encrypted="also-unencrypted")
    category = scan_encrypted_data_inventory(db_session).webhook_credential_secrets
    assert category.total_records == 1
    assert category.encrypted_fields == 0
    assert category.unreadable_records == 1
    assert category.unreadable_fields == 2


def test_operations_secret_inventory_is_bounded_and_discloses_coverage(db_session, seed_users, monkeypatch):
    for _ in range(3):
        add_profile(db_session, seed_users["analyst"].id)
    monkeypatch.setattr(encrypted_data_inventory, "OPERATIONS_INVENTORY_ROW_LIMIT", 2)
    encrypted_data_inventory._clear_operations_encrypted_data_inventory_cache()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if "webhook_credential_profiles" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = encrypted_data_inventory.get_operations_encrypted_data_inventory(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        encrypted_data_inventory._clear_operations_encrypted_data_inventory_cache()
    assert response.inventory.webhook_credential_secrets.total_records == 2
    assert "webhook_credential_secrets" in response.truncated_categories
    assert len(statements) == 1
    assert "LIMIT" in statements[0]
    assert "webhook_credential_profiles.name" not in statements[0]
