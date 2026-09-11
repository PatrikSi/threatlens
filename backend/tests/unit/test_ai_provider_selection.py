from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_provider import AIProviderConfiguration
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.schemas.ai_providers import AIProviderCreate
from app.services import ai_integration
from app.services.ai_config import (
    get_or_create_ai_settings,
    load_active_ai_settings,
    load_public_ai_feature_flags,
)
from app.services.ai_ops import queue_ai_task_run, start_ai_task_run
from app.services.ai_provider_client import AIIntegrationError, call_ai_json
from app.services.ai_provider_selection import (
    PROVIDER_SELECTION_KEY,
    lock_selected_provider,
)
from app.services.ai_providers import create_provider, get_provider_routing
from app.services.ai_request_runtime import _ai_request_fingerprint


@pytest.fixture()
def configured(db_session, monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "legacy-environment-key")
    get_settings.cache_clear()
    settings = get_or_create_ai_settings(db_session)
    settings.base_url = "https://api.openai.com/v1"
    settings.model = "legacy-model"
    db_session.commit()
    return settings


def _provider(db, name="Primary", *, key="isolated-provider-key"):
    provider, _created = create_provider(
        db,
        AIProviderCreate(
            name=name,
            base_url=f"https://{name.lower()}.example/v1",
            model=f"{name}-model",
            api_key=key,
        ),
    )
    db.commit()
    return provider


def _queue(db, *, metadata=None, task_type="daily_brief", parent_run_id=None):
    run = queue_ai_task_run(
        db,
        task_type=task_type,
        trigger_source="manual",
        metadata=metadata,
        parent_run_id=parent_run_id,
    )
    db.commit()
    return run


def test_named_profiles_are_isolated_from_legacy_and_feature_routing(
    db_session, configured
):
    first = _provider(db_session)
    second = _provider(db_session, "Reports", key=None)
    routing = get_provider_routing(db_session)
    routing.default_provider_id = first.id
    routing.report_provider_id = second.id
    db_session.commit()
    item = load_active_ai_settings(db_session, feature_type="item_enrichment")
    report = load_active_ai_settings(db_session, feature_type="report")
    legacy = load_active_ai_settings(db_session, use_legacy=True)
    assert (item.model, item.api_key) == (first.model, "isolated-provider-key")
    assert (report.model, report.api_key) == (second.model, None)
    assert (legacy.model, legacy.api_key) == ("legacy-model", "legacy-environment-key")
    assert "isolated-provider-key" not in repr(item)
    assert "legacy-environment-key" not in repr(legacy)
    assert all(
        (
            load_public_ai_feature_flags(db_session).ai_summary_enabled,
            load_public_ai_feature_flags(db_session).ai_reporting_enabled,
        )
    )


def test_feature_only_provider_is_available_without_legacy_configuration(
    db_session, configured
):
    configured.base_url = None
    configured.model = None
    provider = _provider(db_session)
    get_provider_routing(db_session).report_provider_id = provider.id
    db_session.commit()
    flags = load_public_ai_feature_flags(db_session)
    assert flags.ai_configured and flags.ai_reporting_enabled
    assert not flags.ai_summary_enabled and not flags.ai_daily_brief_enabled


def test_queued_selection_and_reprocess_children_survive_routing_changes(
    db_session, configured
):
    first = _provider(db_session)
    second = _provider(db_session, "Secondary")
    routing = get_provider_routing(db_session)
    routing.default_provider_id = first.id
    db_session.commit()
    parent = _queue(db_session, task_type="reprocess")
    routing.default_provider_id = second.id
    db_session.commit()
    child = _queue(db_session, parent_run_id=parent.id, task_type="item_enrichment")
    selected = load_active_ai_settings(
        db_session, feature_type="item_enrichment", task_run_id=child.id
    )
    assert selected.provider_id == first.id
    assert (
        child.metadata_json[PROVIDER_SELECTION_KEY]
        == parent.metadata_json[PROVIDER_SELECTION_KEY]
    )
    assert "isolated-provider-key" not in json.dumps(child.metadata_json)
    assert load_active_ai_settings(db_session).provider_id == second.id


@pytest.mark.parametrize(
    "change,code",
    [
        ("version", "provider_version_changed"),
        ("disabled", "provider_disabled"),
        ("deleted", "provider_missing"),
        ("credential", "provider_credential_unavailable"),
    ],
)
def test_queued_provider_changes_fail_closed(db_session, configured, change, code):
    provider = _provider(db_session)
    routing = get_provider_routing(db_session)
    routing.default_provider_id = provider.id
    db_session.commit()
    run = _queue(db_session)
    if change == "version":
        provider.version += 1
    elif change == "disabled":
        provider.enabled = False
    elif change == "credential":
        provider.api_key_encrypted = "enc:v1:unreadable"
    else:
        routing.default_provider_id = None
        db_session.flush()
        db_session.delete(provider)
    db_session.commit()
    active = load_active_ai_settings(
        db_session, feature_type="daily_brief", task_run_id=run.id
    )
    assert not active.ai_configured and active.api_key is None
    assert active.configuration_error_code == code
    with pytest.raises(AIIntegrationError):
        lock_selected_provider(db_session, active)


def test_legacy_queued_work_does_not_adopt_new_routes(db_session, configured):
    old = AITaskRun(
        task_type="daily_brief",
        trigger_source="manual",
        status="queued",
        metadata_json={},
    )
    db_session.add(old)
    new_legacy = _queue(db_session)
    provider = _provider(db_session)
    get_provider_routing(db_session).default_provider_id = provider.id
    db_session.commit()
    for run in (old, new_legacy):
        active = load_active_ai_settings(db_session, task_run_id=run.id)
        assert active.provider_id is None and active.model == "legacy-model"


@pytest.mark.parametrize("child_type", ["item_enrichment", "daily_brief"])
def test_legacy_parent_keeps_new_children_on_legacy_provider(
    db_session, configured, child_type
):
    parent = AITaskRun(
        task_type="reprocess",
        trigger_source="manual",
        status="queued",
        metadata_json={},
    )
    db_session.add(parent)
    provider = _provider(db_session)
    get_provider_routing(db_session).default_provider_id = provider.id
    db_session.commit()
    child = _queue(db_session, task_type=child_type, parent_run_id=parent.id)
    active = load_active_ai_settings(
        db_session, task_run_id=child.id, feature_type=child_type
    )
    assert active.provider_id is None and active.model == "legacy-model"


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        "invalid",
        {"provider_id": "bad"},
        {"provider_id": str(uuid.uuid4()), "version": True},
    ],
)
def test_malformed_queue_snapshot_is_not_a_legacy_fallback(
    db_session, configured, snapshot
):
    run = _queue(db_session, metadata={PROVIDER_SELECTION_KEY: snapshot})
    active = load_active_ai_settings(db_session, task_run_id=run.id)
    assert active.configuration_error_code == "provider_selection_invalid"
    assert not active.ai_configured


def test_missing_task_does_not_resolve_current_provider(db_session, configured):
    assert (
        load_active_ai_settings(
            db_session, task_run_id=uuid.uuid4()
        ).configuration_error_code
        == "provider_selection_invalid"
    )


def test_provider_lock_prevents_concurrent_credential_edits(
    db_session, database_engine, configured
):
    with Session(database_engine) as setup:
        provider = _provider(setup, f"Concurrent{uuid.uuid4().hex}")
        provider_id = provider.id
    try:
        active = load_active_ai_settings(db_session, provider_id=provider_id)
        with Session(database_engine) as reader:
            lock_selected_provider(reader, active)
            with Session(database_engine) as writer:
                with pytest.raises(OperationalError):
                    writer.scalar(
                        select(AIProviderConfiguration)
                        .where(AIProviderConfiguration.id == provider_id)
                        .with_for_update(nowait=True)
                    )
                writer.rollback()
            reader.commit()
            with Session(database_engine) as writer:
                writer.execute(
                    update(AIProviderConfiguration)
                    .where(AIProviderConfiguration.id == provider_id)
                    .values(enabled=False)
                )
                writer.commit()
            with pytest.raises(AIIntegrationError, match="disabled"):
                lock_selected_provider(reader, active)
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(AIProviderConfiguration).where(
                    AIProviderConfiguration.id == provider_id
                )
            )
            cleanup.commit()


def test_profile_lock_contention_is_not_sent_and_leaves_session_usable(
    db_session, database_engine, configured
):
    with Session(database_engine) as setup:
        provider = _provider(setup, f"Contended{uuid.uuid4().hex}")
        provider_id = provider.id
    try:
        active = load_active_ai_settings(db_session, provider_id=provider_id)
        with Session(database_engine) as writer, Session(database_engine) as reader:
            writer.scalar(
                select(AIProviderConfiguration)
                .where(AIProviderConfiguration.id == provider_id)
                .with_for_update()
            )
            with pytest.raises(AIIntegrationError) as error:
                lock_selected_provider(reader, active)
            assert error.value.provider_io_outcome == "not_sent"
            assert not error.value.retryable
            assert (
                reader.scalar(
                    select(AIProviderConfiguration.id).where(
                        AIProviderConfiguration.id == provider_id
                    )
                )
                == provider_id
            )
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(AIProviderConfiguration).where(
                    AIProviderConfiguration.id == provider_id
                )
            )
            cleanup.commit()


def test_named_provider_key_only_goes_to_its_origin(db_session, configured):
    provider = _provider(db_session)
    active = load_active_ai_settings(db_session, provider_id=provider.id)
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}
        )

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler))

    assert call_ai_json(active, messages=[], client_factory=factory).payload == {
        "ok": True
    }
    assert seen == [
        ("https://primary.example/v1/chat/completions", "Bearer isolated-provider-key")
    ]
    with pytest.raises(AIIntegrationError, match="destination"):
        call_ai_json(
            replace(active, base_url="https://other.example/v1"),
            messages=[],
            client_factory=factory,
        )
    assert len(seen) == 1


def test_provider_error_does_not_echo_credential(db_session, configured):
    active = load_active_ai_settings(db_session, provider_id=_provider(db_session).id)

    def factory(**kwargs):
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401, json={"error": {"message": "Rejected isolated-provider-key"}}
                )
            )
        )

    with pytest.raises(AIIntegrationError) as error:
        call_ai_json(active, messages=[], client_factory=factory)
    assert "isolated-provider-key" not in str(error.value)
    assert "isolated-provider-key" not in json.dumps(error.value.debug_payload())


@pytest.mark.parametrize("status_code", [200, 401])
def test_provider_cannot_echo_credentials_in_metadata_or_encoded_output(
    db_session, configured, status_code
):
    active = load_active_ai_settings(db_session, provider_id=_provider(db_session).id)
    payload = {
        "model": "isolated-provider-key",
        "isolated-provider-key": True,
        "choices": [
            {
                "finish_reason": "isolated-provider-key",
                "message": {
                    "content": '{"summary_text":"isolated-provi\\u0064er-key"}'
                },
            }
        ],
    }
    if status_code == 401:
        payload["error"] = {
            "message": "Bad authentication",
            "param": "isolated-provider-key",
            "type": "isolated-provider-key",
            "code": "isolated-provider-key",
        }

    def factory(**kwargs):
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, json=payload)
            )
        )

    if status_code == 200:
        result = call_ai_json(active, messages=[], client_factory=factory)
        assert "isolated-provider-key" not in repr(result)
        assert result.payload["summary_text"] == "[redacted]"
    else:
        with pytest.raises(AIIntegrationError) as captured:
            call_ai_json(active, messages=[], client_factory=factory)
        assert "isolated-provider-key" not in json.dumps(captured.value.debug_payload())


def test_legacy_request_fingerprint_remains_byte_compatible(db_session, configured):
    active = load_active_ai_settings(db_session, use_legacy=True)
    original = {
        "feature_type": "daily_brief",
        "messages": [],
        "item_id": None,
        "daily_brief_id": None,
        "report_id": None,
        "provider_type": active.provider_type,
        "base_url": active.base_url,
        "model": active.model,
        "temperature": active.temperature,
        "max_tokens": 128,
        "stream": False,
    }
    expected = hashlib.sha256(
        json.dumps(
            original, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    kwargs = dict(
        feature_type="daily_brief",
        messages=[],
        item_id=None,
        daily_brief_id=None,
        report_id=None,
        requested_max_tokens=128,
    )
    assert _ai_request_fingerprint(active=active, **kwargs) == expected
    assert (
        _ai_request_fingerprint(
            active=replace(active, provider_id=uuid.uuid4(), provider_version=1),
            **kwargs,
        )
        != expected
    )


def test_profile_fence_failure_settles_receipt_without_provider_io(
    db_session, configured, monkeypatch
):
    provider = _provider(db_session)
    active = load_active_ai_settings(db_session, provider_id=provider.id)
    run = _queue(
        db_session,
        task_type="connection_test",
        metadata={
            PROVIDER_SELECTION_KEY: {
                "provider_id": str(provider.id),
                "version": provider.version,
            }
        },
    )
    start_ai_task_run(db_session, run_id=run.id, worker_name="test")
    db_session.commit()
    provider.enabled = False
    db_session.commit()
    monkeypatch.setattr(
        ai_integration,
        "_call_ai_json",
        lambda *args, **kwargs: pytest.fail("provider must not be called"),
    )
    result = ai_integration.test_ai_connection(
        db_session, task_run_id=run.id, active_settings=active
    )
    assert not result.success and "disabled" in result.error
    receipt = db_session.scalar(
        select(AIProviderAttemptReceipt).where(
            AIProviderAttemptReceipt.task_run_id_snapshot == run.id
        )
    )
    assert receipt is not None and receipt.state not in {"reserved", "ambiguous"}
