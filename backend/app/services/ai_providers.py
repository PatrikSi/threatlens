"""Provider persistence with one lock order: routing, then provider UUIDs."""

from __future__ import annotations

import hmac
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.ai_endpoints import ai_endpoint_origin
from app.models.ai_provider import (
    AIProviderConfiguration,
    AIProviderRetiredID,
    AIProviderRouting,
)
from app.schemas.ai_providers import (
    AIProviderCreate,
    AIProviderFields,
    AIProviderResponse,
    AIProviderRoutingResponse,
    AIProviderRoutingUpdate,
    AIProviderUpdate,
    AIProviderWrite,
)
from app.schemas.ai_provider_capabilities import CAPABILITY_FIELDS
from app.schemas.ai_provider_admission import ADMISSION_FIELDS
from app.services.secret_storage import decrypt_text, encrypt_text, is_encrypted_text

PROVIDER_FIELDS = tuple(AIProviderFields.model_fields)
ROUTING_FIELDS = (
    "default_provider_id",
    "item_enrichment_provider_id",
    "daily_brief_provider_id",
    "report_provider_id",
)
_CREDENTIAL_ERROR = (
    "The saved API key cannot be decrypted. Restore the application data encryption "
    "key, replace this provider's API key, or explicitly clear it."
)


class AIProviderError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def get_provider_routing(db: Session, *, for_update: bool = False) -> AIProviderRouting:
    query = select(AIProviderRouting).where(AIProviderRouting.singleton_key == 1)
    if for_update:
        query = query.with_for_update()
    routing = db.scalar(query.execution_options(populate_existing=True))
    if routing is None:
        db.execute(
            insert(AIProviderRouting)
            .values(singleton_key=1, version=1)
            .on_conflict_do_nothing()
        )
        routing = db.scalar(query.execution_options(populate_existing=True))
    assert routing is not None
    return routing


def get_provider(
    db: Session,
    provider_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AIProviderConfiguration:
    query = select(AIProviderConfiguration).where(
        AIProviderConfiguration.id == provider_id
    )
    if for_update:
        query = query.with_for_update()
    provider = db.scalar(query.execution_options(populate_existing=True))
    if provider is None:
        raise AIProviderError(
            "provider_not_found",
            "The AI provider no longer exists. Refresh provider settings.",
            status_code=404,
        )
    return provider


def resolve_provider(
    db: Session, feature_type: str | None = None
) -> AIProviderConfiguration | None:
    routing = get_provider_routing(db)
    field = {
        "item_enrichment": "item_enrichment_provider_id",
        "daily_brief": "daily_brief_provider_id",
        "report": "report_provider_id",
    }.get(feature_type or "")
    provider_id = (
        getattr(routing, field) if field else None
    ) or routing.default_provider_id
    return get_provider(db, provider_id) if provider_id is not None else None


def read_provider_api_key(
    provider: AIProviderConfiguration,
) -> tuple[str | None, str | None]:
    if provider.api_key_encrypted is None:
        return None, None
    # New provider credentials have no plaintext legacy representation.
    if not is_encrypted_text(provider.api_key_encrypted):
        return None, _CREDENTIAL_ERROR
    try:
        key = decrypt_text(provider.api_key_encrypted)
    except (ValueError, TypeError):
        return None, _CREDENTIAL_ERROR
    if not key or any(ord(char) < 32 or ord(char) > 126 for char in key):
        return None, _CREDENTIAL_ERROR
    return key, None


def provider_response(provider: AIProviderConfiguration) -> AIProviderResponse:
    _key, error = read_provider_api_key(provider)
    return AIProviderResponse(
        **{field: getattr(provider, field) for field in PROVIDER_FIELDS},
        id=provider.id,
        version=provider.version,
        api_key_configured=provider.api_key_encrypted is not None,
        credential_error=error,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def routing_response(routing: AIProviderRouting) -> AIProviderRoutingResponse:
    return AIProviderRoutingResponse(
        version=routing.version,
        **{field: getattr(routing, field) for field in ROUTING_FIELDS},
    )


def list_providers(
    db: Session, *, search: str, limit: int, offset: int
) -> tuple[list[AIProviderConfiguration], int]:
    query = select(AIProviderConfiguration)
    if search.strip():
        query = query.where(
            AIProviderConfiguration.normalized_name.contains(
                search.strip().casefold(), autoescape=True
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        db.scalars(
            query.order_by(
                AIProviderConfiguration.normalized_name, AIProviderConfiguration.id
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def create_provider(
    db: Session, payload: AIProviderCreate
) -> tuple[AIProviderConfiguration, bool]:
    get_provider_routing(db, for_update=True)
    if db.get(AIProviderRetiredID, payload.id) is not None:
        raise AIProviderError(
            "provider_id_retired",
            "This provider identifier was permanently retired when the provider was "
            "deleted. Start a new provider with a new identifier.",
        )
    existing = db.get(AIProviderConfiguration, payload.id, populate_existing=True)
    if existing is not None:
        if _matches_creation(existing, payload):
            return existing, False
        raise AIProviderError(
            "provider_id_conflict",
            "This create request ID already belongs to a different or subsequently edited provider. Refresh the provider before retrying.",
        )
    _require_unique_name(db, name=payload.name)
    provider = AIProviderConfiguration(
        id=payload.id, normalized_name=payload.name.casefold(), version=1
    )
    _apply_provider_fields(provider, payload)
    db.add(provider)
    db.flush()
    return provider, True


def update_provider(
    db: Session, provider_id: uuid.UUID, payload: AIProviderUpdate
) -> AIProviderConfiguration:
    get_provider_routing(db, for_update=True)
    provider = get_provider(db, provider_id, for_update=True)
    require_provider_version(provider.version, payload.version)
    _require_unique_name(db, name=payload.name, excluding=provider.id)
    if (
        provider.api_key_encrypted is not None
        and _origin(provider.base_url) != _origin(payload.base_url)
        and payload.api_key is None
        and not payload.clear_api_key
    ):
        raise AIProviderError(
            "provider_credential_destination_changed",
            "Changing the endpoint origin requires replacing or explicitly clearing the saved API key.",
        )
    _apply_provider_fields(provider, payload)
    provider.version += 1
    db.flush()
    return provider


def delete_provider(db: Session, provider_id: uuid.UUID, *, version: int) -> None:
    routing = get_provider_routing(db, for_update=True)
    provider = get_provider(db, provider_id, for_update=True)
    require_provider_version(provider.version, version)
    if any(getattr(routing, field) == provider.id for field in ROUTING_FIELDS):
        raise AIProviderError(
            "provider_in_use",
            "This provider is assigned to AI features. Update provider routing before deleting it.",
        )
    # Keep only its identifier, never its endpoint or credential. Task history
    # may be pruned independently without allowing this identity to be reused.
    db.add(AIProviderRetiredID(id=provider.id))
    db.delete(provider)
    db.flush()


def update_provider_routing(
    db: Session, payload: AIProviderRoutingUpdate
) -> AIProviderRouting:
    routing = get_provider_routing(db, for_update=True)
    require_provider_version(routing.version, payload.version)
    selected_ids = sorted(
        {getattr(payload, field) for field in ROUTING_FIELDS} - {None}
    )
    for provider_id in selected_ids:
        provider = get_provider(db, provider_id, for_update=True)
        if not provider.enabled:
            raise AIProviderError(
                "provider_disabled",
                "Enable the selected AI provider before assigning it.",
            )
        _key, error = read_provider_api_key(provider)
        if error:
            raise AIProviderError("provider_credential_unreadable", error)
    for field in ROUTING_FIELDS:
        setattr(routing, field, getattr(payload, field))
    routing.version += 1
    db.flush()
    return routing


def require_provider_version(actual: int, expected: int) -> None:
    if actual != expected:
        raise AIProviderError(
            "provider_version_conflict",
            "These AI settings changed while you were editing. Reload the latest settings and review your changes before saving again.",
        )


def _apply_provider_fields(
    provider: AIProviderConfiguration, payload: AIProviderWrite
) -> None:
    for field in PROVIDER_FIELDS:
        if isinstance(payload, AIProviderUpdate) and field in (*CAPABILITY_FIELDS, *ADMISSION_FIELDS) and field not in payload.model_fields_set:
            continue
        setattr(provider, field, getattr(payload, field))
    provider.normalized_name = payload.name.casefold()
    if payload.api_key is not None:
        try:
            provider.api_key_encrypted = encrypt_text(
                payload.api_key.get_secret_value()
            )
        except ValueError as exc:
            raise AIProviderError(
                "provider_credential_storage_unavailable",
                "The API key could not be stored. Check the application data encryption key configuration and retry.",
                status_code=503,
            ) from exc
    elif payload.clear_api_key:
        provider.api_key_encrypted = None


def _matches_creation(
    provider: AIProviderConfiguration, payload: AIProviderCreate
) -> bool:
    if provider.version != 1 or any(
        getattr(provider, field) != getattr(payload, field) for field in PROVIDER_FIELDS
    ):
        return False
    key, error = read_provider_api_key(provider)
    desired = (
        payload.api_key.get_secret_value() if payload.api_key is not None else None
    )
    if error or (key is None) != (desired is None):
        return False
    return key is None or hmac.compare_digest(key.encode(), desired.encode())


def _require_unique_name(
    db: Session, *, name: str, excluding: uuid.UUID | None = None
) -> None:
    query = select(AIProviderConfiguration.id).where(
        AIProviderConfiguration.normalized_name == name.casefold()
    )
    if excluding is not None:
        query = query.where(AIProviderConfiguration.id != excluding)
    if db.scalar(query) is not None:
        raise AIProviderError(
            "provider_name_conflict",
            "An AI provider with this name already exists. Choose a different name.",
        )


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        return ai_endpoint_origin(url)
    except ValueError:
        # A malformed saved destination has no trusted origin. Replacing or
        # clearing its credential lets an administrator repair the endpoint.
        return None
