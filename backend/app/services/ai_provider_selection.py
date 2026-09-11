"""Resolve provider assignments without moving credentials into task metadata.

An assignment is captured when work is queued. Edits to a selected profile stop
that work before its next request; they never change its destination implicitly.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.ai_endpoints import ai_endpoint_origin, validate_chat_completion_endpoint
from app.core.config import get_settings
from app.models.ai_provider import AIProviderConfiguration, AIProviderRouting
from app.models.ai_task_run import AITaskRun
from app.services.ai_providers import read_provider_api_key

if TYPE_CHECKING:
    from app.services.ai_config import ActiveAISettings

PROVIDER_SELECTION_KEY = "provider_selection"
_FEATURE_FIELDS = {
    "item_enrichment": "item_enrichment_provider_id",
    "daily_brief": "daily_brief_provider_id",
    "report": "report_provider_id",
}


def provider_origin(base_url: str) -> str:
    scheme, host, port = ai_endpoint_origin(base_url)
    return f"{scheme}://{host}:{port}"


def _assigned_provider_id(db: Session, feature_type: str | None) -> uuid.UUID | None:
    routing = db.get(AIProviderRouting, 1)
    if routing is None:
        return None
    field_name = _FEATURE_FIELDS.get(feature_type or "")
    override = getattr(routing, field_name) if field_name else None
    return override or routing.default_provider_id


def provider_selection_metadata(
    db: Session,
    *,
    task_type: str,
    metadata: dict[str, object] | None,
    parent_run_id: uuid.UUID | None,
) -> dict[str, object]:
    result = dict(metadata or {})
    if PROVIDER_SELECTION_KEY in result or not get_settings().ai_enabled:
        return result
    if parent_run_id is not None:
        parent = db.get(AITaskRun, parent_run_id)
        if parent is not None:
            result[PROVIDER_SELECTION_KEY] = (parent.metadata_json or {}).get(
                PROVIDER_SELECTION_KEY,
                {"provider_id": None, "version": None, "model": None},
            )
            return result
    feature = task_type
    if task_type == "reprocess":
        feature = (
            "daily_brief"
            if result.get("scope") == "daily_brief_backfill"
            else "item_enrichment"
        )
    if feature not in _FEATURE_FIELDS:
        # The existing connection test deliberately tests the legacy settings.
        return result
    selected_id = _assigned_provider_id(db, feature)
    provider = db.get(AIProviderConfiguration, selected_id) if selected_id else None
    result[PROVIDER_SELECTION_KEY] = {
        "provider_id": str(selected_id) if selected_id else None,
        "version": provider.version if provider else None,
        "model": provider.model if provider else None,
    }
    return result


def apply_provider_selection(
    db: Session,
    active: ActiveAISettings,
    *,
    feature_type: str | None,
    task_run_id: uuid.UUID | None,
    provider_id: uuid.UUID | None,
) -> ActiveAISettings:
    selected_id = provider_id
    expected_version = None
    if provider_id is None and task_run_id is not None:
        run = db.get(AITaskRun, task_run_id)
        if run is None:
            return _unavailable(
                active,
                "provider_selection_invalid",
                "The queued AI task no longer exists. Start a new task.",
            )
        metadata = run.metadata_json or {}
        if PROVIDER_SELECTION_KEY not in metadata:
            # Work accepted before provider routing existed retains legacy routing.
            return active
        if PROVIDER_SELECTION_KEY in metadata:
            snapshot = metadata[PROVIDER_SELECTION_KEY]
            if not isinstance(snapshot, dict) or "provider_id" not in snapshot:
                return _unavailable(
                    active,
                    "provider_selection_invalid",
                    "The queued AI provider selection is invalid. Start a new task.",
                )
            if snapshot["provider_id"] is None:
                return active
            try:
                selected_id = uuid.UUID(str(snapshot["provider_id"]))
                expected_version = snapshot["version"]
                if type(expected_version) is not int or expected_version < 1:
                    raise ValueError("invalid version")
            except (ValueError, TypeError, KeyError):
                return _unavailable(
                    active,
                    "provider_selection_invalid",
                    "The queued AI provider selection is invalid. Start a new task.",
                )
    if selected_id is None:
        selected_id = _assigned_provider_id(db, feature_type)
    if selected_id is None:
        return active
    provider = db.get(AIProviderConfiguration, selected_id)
    if provider is None:
        return _unavailable(
            replace(active, provider_id=selected_id),
            "provider_missing",
            "The selected AI provider was removed. Choose a provider and start a new task.",
        )
    active = replace(
        active,
        provider_id=provider.id,
        provider_version=provider.version,
        provider_name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        model=provider.model,
        api_key=None,
        credential_origin=None,
        temperature=provider.temperature,
        max_completion_tokens=provider.max_completion_tokens,
        request_timeout_seconds=provider.request_timeout_seconds,
        request_max_retries=provider.request_max_retries,
    )
    if expected_version is not None and provider.version != expected_version:
        return _unavailable(
            active,
            "provider_version_changed",
            "The selected AI provider changed after this task was queued. Review its settings and start a new task.",
        )
    if not provider.enabled:
        return _unavailable(
            active,
            "provider_disabled",
            "The selected AI provider is disabled. Enable it or choose another provider for new tasks.",
        )
    try:
        validate_chat_completion_endpoint(provider.base_url)
        active = replace(active, credential_origin=provider_origin(provider.base_url))
    except ValueError as exc:
        return _unavailable(active, "provider_endpoint_invalid", str(exc))
    api_key, credential_error = read_provider_api_key(provider)
    if credential_error:
        return _unavailable(
            active,
            "provider_credential_unavailable",
            "The selected AI provider credential cannot be decrypted. Restore the encryption key or replace the credential in AI settings.",
        )
    return replace(active, ai_configured=active.ai_enabled, api_key=api_key)


def _unavailable(active: ActiveAISettings, code: str, message: str) -> ActiveAISettings:
    return replace(
        active,
        ai_configured=False,
        api_key=None,
        configuration_error=message,
        configuration_error_code=code,
    )


def lock_selected_provider(db: Session, active: ActiveAISettings) -> None:
    """Lock after IAM/data/receipt fences and hold until attempt settlement.

    The profile lock is shared: requests can run concurrently, while credential
    replacement and disable wait for already-authorized, deadline-bounded I/O.
    """
    from app.services.ai_provider_client import (
        AIIntegrationError,
        AI_PROVIDER_IO_NOT_SENT,
    )

    error = getattr(active, "configuration_error", None)
    provider_id = getattr(active, "provider_id", None)
    if error is None and provider_id is not None:
        try:
            with db.begin_nested():
                provider = db.scalar(
                    select(AIProviderConfiguration)
                    .where(AIProviderConfiguration.id == provider_id)
                    .with_for_update(read=True, nowait=True)
                    .execution_options(populate_existing=True)
                )
        except SQLAlchemyError as exc:
            raise AIIntegrationError(
                "The selected AI provider is being updated or its state is unavailable. Retry after the settings update completes.",
                retryable=False,
                provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
            ) from exc
        if (
            provider is None
            or not provider.enabled
            or provider.version != active.provider_version
        ):
            error = "The selected AI provider changed or was disabled before this request. Review its settings and start a new task."
        else:
            api_key, credential_error = read_provider_api_key(provider)
            if credential_error or api_key != active.api_key:
                error = "The selected AI provider credential is unavailable or changed. Review AI settings and start a new task."
    if error:
        raise AIIntegrationError(
            error, retryable=False, provider_io_outcome=AI_PROVIDER_IO_NOT_SENT
        )
