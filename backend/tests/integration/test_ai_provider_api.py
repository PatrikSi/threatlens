import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_READ_ITEMS
from app.models.ai_provider import AIProviderConfiguration, AIProviderRetiredID
from app.models.ai_task_run import AITaskRun
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog
from app.services.ai_providers import read_provider_api_key, resolve_provider
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from app.services.secret_storage import decrypt_text


@pytest.fixture(autouse=True)
def enable_ai(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "true")
    get_settings.cache_clear()


def _payload(**changes):
    return {
        "name": "Local model",
        "base_url": "http://localhost:11434/v1",
        "model": "local-model",
        **changes,
    }


def _create(client, auth_headers, **changes):
    response = client.post(
        "/ai/providers", json=_payload(**changes), headers=auth_headers["admin"]
    )
    assert response.status_code == 201, response.text
    return response.json()


def _update_payload(provider, **changes):
    return {
        field: value
        for field, value in provider.items()
        if field
        not in {
            "id",
            "api_key_configured",
            "credential_error",
            "created_at",
            "updated_at",
        }
    } | changes


def test_catalog_crud_is_versioned_and_legacy_configuration_is_unchanged(
    client, auth_headers
):
    before = client.get("/ai/settings", headers=auth_headers["admin"]).json()
    provider = _create(client, auth_headers)
    assert provider["version"] == 1
    assert not provider["api_key_configured"]
    changed = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(provider, model="new-model"),
        headers=auth_headers["admin"],
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    stale = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(provider),
        headers=auth_headers["admin"],
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "provider_version_conflict"
    assert stale.json()["error"]["code"] == "provider_version_conflict"
    stale_delete = client.delete(
        f"/ai/providers/{provider['id']}?version=1", headers=auth_headers["admin"]
    )
    assert stale_delete.status_code == 409
    assert (
        client.delete(
            f"/ai/providers/{provider['id']}?version=2", headers=auth_headers["admin"]
        ).status_code
        == 204
    )
    assert (
        client.get(
            f"/ai/providers/{provider['id']}", headers=auth_headers["admin"]
        ).status_code
        == 404
    )
    after = client.get("/ai/settings", headers=auth_headers["admin"]).json()
    assert before == after


def test_provider_key_is_encrypted_retained_cleared_and_never_audited(
    client, auth_headers, db_session, monkeypatch
):
    monkeypatch.setenv("AI_API_KEY", "legacy-environment-key")
    get_settings.cache_clear()
    provider = _create(client, auth_headers, api_key="named-provider-secret")
    stored = db_session.get(AIProviderConfiguration, uuid.UUID(provider["id"]))
    assert stored.api_key_encrypted.startswith("enc:v1:")
    assert "named-provider-secret" not in stored.api_key_encrypted
    assert decrypt_text(stored.api_key_encrypted) == "named-provider-secret"
    assert "named-provider-secret" not in str(provider)
    retained = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(provider, api_key=None, model="changed"),
        headers=auth_headers["admin"],
    )
    assert retained.status_code == 200
    assert retained.json()["api_key_configured"] is True
    cleared = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(retained.json(), clear_api_key=True),
        headers=auth_headers["admin"],
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_configured"] is False
    db_session.refresh(stored)
    assert read_provider_api_key(stored) == (None, None)
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action.like("ai.provider.%"))
    ).all()
    assert len(audits) == 3
    assert "named-provider-secret" not in str([audit.metadata_json for audit in audits])


def test_saved_credential_requires_explicit_action_when_origin_changes(
    client, auth_headers
):
    provider = _create(client, auth_headers, api_key="secret")
    blocked = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(provider, base_url="http://localhost:9999/v1"),
        headers=auth_headers["admin"],
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "provider_credential_destination_changed"
    same_origin = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(provider, base_url="http://LOCALHOST:11434/other"),
        headers=auth_headers["admin"],
    )
    assert same_origin.status_code == 200
    changed = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(
            same_origin.json(), base_url="http://localhost:9999/v1", clear_api_key=True
        ),
        headers=auth_headers["admin"],
    )
    assert changed.status_code == 200


def test_uncertain_create_retry_is_idempotent_and_cannot_overwrite(
    client, auth_headers, db_session
):
    payload = _payload(id=str(uuid.uuid4()), api_key="secret")
    first = client.post("/ai/providers", json=payload, headers=auth_headers["admin"])
    assert first.status_code == 201
    replay = client.post("/ai/providers", json=payload, headers=auth_headers["admin"])
    assert replay.status_code == 200
    assert replay.json() == first.json()
    mismatch = client.post(
        "/ai/providers",
        json=payload | {"api_key": "different"},
        headers=auth_headers["admin"],
    )
    assert mismatch.status_code == 409
    changed = client.put(
        f"/ai/providers/{payload['id']}",
        json=_update_payload(first.json(), model="new"),
        headers=auth_headers["admin"],
    )
    assert changed.status_code == 200
    assert (
        client.post(
            "/ai/providers", json=payload, headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    assert (
        len(
            db_session.scalars(
                select(AuditLog).where(AuditLog.action == "ai.provider.create")
            ).all()
        )
        == 1
    )


def test_deleted_provider_id_is_retired_without_relying_on_task_history(
    client, auth_headers, db_session
):
    provider = _create(client, auth_headers, api_key="retired-secret")
    deleted = client.delete(
        f"/ai/providers/{provider['id']}?version=1", headers=auth_headers["admin"]
    )
    assert deleted.status_code == 204
    retired = db_session.get(AIProviderRetiredID, uuid.UUID(provider["id"]))
    assert retired is not None and retired.retired_at is not None
    assert db_session.scalar(select(AITaskRun.id)) is None
    assert db_session.get(AIProviderConfiguration, retired.id) is None

    recreated = client.post(
        "/ai/providers",
        headers=auth_headers["admin"],
        json=_payload(
            id=provider["id"], base_url="http://localhost:9999/v1", api_key="new-secret"
        ),
    )
    assert recreated.status_code == 409
    assert recreated.json()["error"]["code"] == "provider_id_retired"
    assert "new identifier" in recreated.json()["error"]["message"]
    assert "new-secret" not in recreated.text

    replacement = _create(client, auth_headers)
    assert replacement["id"] != provider["id"]
    assert replacement["name"] == provider["name"]


def test_retired_provider_cannot_retarget_old_queued_work(
    client, auth_headers, db_session, monkeypatch
):
    from app.services.ai_config import load_active_ai_settings
    from app.services.ai_integration import run_item_ai_enrichment
    from app.services.ai_ops import queue_ai_task_run

    provider = _create(client, auth_headers)
    routing = client.get("/ai/provider-routing", headers=auth_headers["admin"]).json()
    selected = client.put(
        "/ai/provider-routing",
        headers=auth_headers["admin"],
        json=routing | {"default_provider_id": provider["id"]},
    )
    assert selected.status_code == 200
    old_run = queue_ai_task_run(
        db_session, task_type="item_enrichment", trigger_source="manual"
    )
    db_session.commit()
    assert old_run.metadata_json["provider_selection"]["provider_id"] == provider["id"]
    cleared = client.put(
        "/ai/provider-routing",
        headers=auth_headers["admin"],
        json={"version": selected.json()["version"]},
    )
    assert cleared.status_code == 200
    assert (
        client.delete(
            f"/ai/providers/{provider['id']}?version=1", headers=auth_headers["admin"]
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/ai/providers",
            headers=auth_headers["admin"],
            json=_payload(id=provider["id"], base_url="http://localhost:9999/v1"),
        ).status_code
        == 409
    )
    replacement = _create(client, auth_headers, base_url="http://localhost:9999/v1")
    reassigned = client.put(
        "/ai/provider-routing",
        headers=auth_headers["admin"],
        json=cleared.json() | {"default_provider_id": replacement["id"]},
    )
    assert reassigned.status_code == 200

    def unexpected_provider_call(*args, **kwargs):
        pytest.fail("Retired provider work must not reach a replacement endpoint")

    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", unexpected_provider_call
    )
    result = run_item_ai_enrichment(
        db_session, item_id=uuid.uuid4(), task_run_id=old_run.id
    )
    assert result.reason == "provider_missing"
    new_run = queue_ai_task_run(
        db_session, task_type="item_enrichment", trigger_source="manual"
    )
    db_session.commit()
    active = load_active_ai_settings(
        db_session, feature_type="item_enrichment", task_run_id=new_run.id
    )
    assert str(active.provider_id) == replacement["id"]


def test_failed_provider_deletion_rolls_back_identity_retirement(
    client, auth_headers, db_session, monkeypatch
):
    from app.db.budgets import DatabaseDeadlineExceeded

    provider = _create(client, auth_headers)

    def fail_after_deletion(*args, **kwargs):
        raise DatabaseDeadlineExceeded("Synthetic audit deadline")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "app.api.routes.ai_providers._audit_provider", fail_after_deletion
        )
        response = client.delete(
            f"/ai/providers/{provider['id']}?version=1", headers=auth_headers["admin"]
        )
    assert response.status_code == 503
    assert db_session.get(AIProviderRetiredID, uuid.UUID(provider["id"])) is None
    assert (
        client.get(
            f"/ai/providers/{provider['id']}", headers=auth_headers["admin"]
        ).status_code
        == 200
    )
    replay = client.post(
        "/ai/providers",
        headers=auth_headers["admin"],
        json=_payload(id=provider["id"]),
    )
    assert replay.status_code == 200


def test_search_pagination_and_casefolded_unique_names(client, auth_headers):
    for name in ["Zulu", "Alpha", "alphabet", "Literal%"]:
        _create(client, auth_headers, name=name)
    page = client.get(
        "/ai/providers?search=ALP&limit=1&offset=1", headers=auth_headers["admin"]
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert [provider["name"] for provider in page.json()["items"]] == ["alphabet"]
    escaped = client.get(
        "/ai/providers?search=%25", headers=auth_headers["admin"]
    ).json()
    assert escaped["total"] == 1
    duplicate = client.post(
        "/ai/providers", json=_payload(name=" ALPHA "), headers=auth_headers["admin"]
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "provider_name_conflict"


def test_routing_inheritance_conflicts_and_referenced_delete(
    client, auth_headers, db_session
):
    first = _create(client, auth_headers, name="Default")
    second = _create(client, auth_headers, name="Reports")
    routing = client.get("/ai/provider-routing", headers=auth_headers["admin"]).json()
    assert routing["default_provider_id"] is None
    assert resolve_provider(db_session, "report") is None
    updated = client.put(
        "/ai/provider-routing",
        json=routing
        | {"default_provider_id": first["id"], "report_provider_id": second["id"]},
        headers=auth_headers["admin"],
    )
    assert updated.status_code == 200
    assert resolve_provider(db_session, "report").id == uuid.UUID(second["id"])
    assert resolve_provider(db_session, "item_enrichment").id == uuid.UUID(first["id"])
    assert (
        client.put(
            "/ai/provider-routing", json=routing, headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    blocked = client.delete(
        f"/ai/providers/{first['id']}?version=1", headers=auth_headers["admin"]
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "provider_in_use"
    reset = client.put(
        "/ai/provider-routing",
        json={"version": updated.json()["version"]},
        headers=auth_headers["admin"],
    )
    assert reset.status_code == 200
    assert resolve_provider(db_session, "report") is None


def test_disabled_and_missing_providers_cannot_be_assigned(client, auth_headers):
    provider = _create(client, auth_headers, enabled=False)
    routing = client.get("/ai/provider-routing", headers=auth_headers["admin"]).json()
    disabled = client.put(
        "/ai/provider-routing",
        json=routing | {"default_provider_id": provider["id"]},
        headers=auth_headers["admin"],
    )
    assert disabled.status_code == 409
    missing = client.put(
        "/ai/provider-routing",
        json=routing | {"default_provider_id": str(uuid.uuid4())},
        headers=auth_headers["admin"],
    )
    assert missing.status_code == 404


def test_unreadable_credentials_are_visible_in_inventory_and_can_be_replaced(
    client, auth_headers, db_session, monkeypatch
):
    provider = _create(client, auth_headers, api_key="secret")
    inventory = scan_encrypted_data_inventory(db_session)
    assert inventory.ai_provider_secrets.encrypted_fields == 1
    previous_count = inventory.summary.encrypted_fields
    monkeypatch.setenv(
        "APP_DATA_ENCRYPTION_KEY",
        "a-different-encryption-key-with-at-least-32-characters",
    )
    get_settings.cache_clear()
    response = client.get(
        f"/ai/providers/{provider['id']}", headers=auth_headers["admin"]
    )
    assert response.status_code == 200
    assert "cannot be decrypted" in response.json()["credential_error"]
    inventory = scan_encrypted_data_inventory(db_session)
    assert inventory.ai_provider_secrets.unreadable_fields == 1
    assert inventory.summary.encrypted_fields == previous_count
    assert inventory.summary.unreadable_fields >= 1
    replaced = client.put(
        f"/ai/providers/{provider['id']}",
        json=_update_payload(response.json(), api_key="replacement"),
        headers=auth_headers["admin"],
    )
    assert replaced.status_code == 200
    assert replaced.json()["credential_error"] is None


def test_catalog_survives_private_network_policy_change(
    client, auth_headers, monkeypatch
):
    provider = _create(client, auth_headers)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_AI", "false")
    get_settings.cache_clear()
    response = client.get("/ai/providers", headers=auth_headers["admin"])
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == provider["id"]


def test_admin_and_token_scopes_are_required(
    client, auth_headers, db_session, seed_users
):
    for role in ("viewer", "analyst"):
        assert (
            client.get("/ai/providers", headers=auth_headers[role]).status_code == 403
        )
        assert (
            client.post(
                "/ai/providers", json=_payload(), headers=auth_headers[role]
            ).status_code
            == 403
        )
    token = db_session.scalar(
        select(ApiToken).where(ApiToken.user_id == seed_users["admin"].id)
    )
    token.scopes = [SCOPE_READ_AI]
    db_session.commit()
    assert client.get("/ai/providers", headers=auth_headers["admin"]).status_code == 200
    assert (
        client.post(
            "/ai/providers", json=_payload(), headers=auth_headers["admin"]
        ).status_code
        == 403
    )
    token.scopes = [SCOPE_READ_ITEMS]
    db_session.commit()
    assert client.get("/ai/providers", headers=auth_headers["admin"]).status_code == 403


def test_validation_never_echoes_submitted_secret(client, auth_headers):
    secret = "secret-never-echoed"
    response = client.post(
        "/ai/providers",
        json=_payload(api_key=secret, clear_api_key=True),
        headers=auth_headers["admin"],
    )
    assert response.status_code == 422
    assert secret not in response.text


def test_corrupt_plaintext_provider_secret_fails_closed_and_is_counted(
    client, auth_headers, db_session
):
    provider = _create(client, auth_headers)
    stored = db_session.get(AIProviderConfiguration, uuid.UUID(provider["id"]))
    stored.api_key_encrypted = "invalid-plaintext-ciphertext"
    db_session.commit()
    response = client.get(
        f"/ai/providers/{provider['id']}", headers=auth_headers["admin"]
    )
    assert response.status_code == 200
    assert response.json()["credential_error"] is not None
    assert read_provider_api_key(stored)[0] is None
    inventory = scan_encrypted_data_inventory(db_session)
    assert inventory.ai_provider_secrets.unreadable_fields == 1
    assert inventory.summary.unreadable_fields >= 1


def test_provider_connection_contention_returns_retryable_configuration_error(
    client, auth_headers, monkeypatch
):
    from app.db.budgets import DatabaseDeadlineExceeded

    def busy(_db, _payload):
        raise DatabaseDeadlineExceeded("internal timeout detail")

    monkeypatch.setattr("app.api.routes.ai_providers.create_provider", busy)
    response = client.post(
        "/ai/providers",
        json=_payload(api_key="hidden-key"),
        headers=auth_headers["admin"],
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_unavailable"
    assert response.json()["error"]["code"] == "provider_configuration_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert response.headers["retry-after"] == "2"
    assert "hidden-key" not in response.text
    assert "internal timeout detail" not in response.text


def test_concurrent_provider_edits_have_bounded_waits_and_recheck_versions(
    database_engine,
):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from app.db.budgets import DatabaseDeadlineExceeded, database_operation
    from app.schemas.ai_providers import AIProviderCreate, AIProviderUpdate
    from app.services.ai_providers import (
        AIProviderError,
        create_provider,
        update_provider,
    )

    provider_id = uuid.uuid4()
    name = f"concurrent-{provider_id}"
    with Session(database_engine) as owner:
        create_provider(owner, AIProviderCreate(id=provider_id, **_payload(name=name)))
        owner.commit()
        try:
            update_provider(
                owner,
                provider_id,
                AIProviderUpdate(
                    version=1, **_payload(name=name, model="winning-edit")
                ),
            )
            with Session(database_engine) as contender:
                with pytest.raises((OperationalError, DatabaseDeadlineExceeded)):
                    with database_operation(
                        contender, operation="interactive", timeout_seconds=0.1
                    ):
                        update_provider(
                            contender,
                            provider_id,
                            AIProviderUpdate(
                                version=1, **_payload(name=name, model="losing-edit")
                            ),
                        )
            owner.commit()
            with Session(database_engine) as contender:
                with pytest.raises(AIProviderError) as error:
                    update_provider(
                        contender,
                        provider_id,
                        AIProviderUpdate(
                            version=1, **_payload(name=name, model="losing-edit")
                        ),
                    )
                assert error.value.code == "provider_version_conflict"
            assert (
                owner.get(AIProviderConfiguration, provider_id).model == "winning-edit"
            )
        finally:
            owner.rollback()
            owner.delete(owner.get(AIProviderConfiguration, provider_id))
            owner.commit()
