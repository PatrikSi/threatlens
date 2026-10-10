from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from sqlalchemy import literal, select, union_all
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.feed import Feed
from app.models.ai_article_continuation import AIArticleContinuation
from app.models.ai_qualification import AIQualification
from app.models.publication_consumer import PublicationConsumer
from app.models.ai_provider import AIProviderConfiguration
from app.models.integration import IntegrationInstance
from app.models.mfa import UserRecoveryCode, UserTOTPCredential
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.oidc import OIDCProvider
from app.models.team_item_assessment import TeamAssessmentRevision, TeamItemAssessment
from app.models.webhook_credential import WebhookCredentialProfile
from app.schemas.health import (
    EncryptedDataInventoryCategory,
    EncryptedDataInventoryResponse,
    EncryptedDataInventorySummary,
    EncryptedDataStartupScan,
    RecoveryCodeHashInventory,
)
from app.services.feed_storage import try_decrypt_feed_url
from app.services.notification_webhook_storage import (
    decrypt_notification_json,
    decrypt_notification_text,
)
from app.services.secret_storage import (
    configured_hashing_key_ids,
    decrypt_text,
    is_encrypted_json,
    is_encrypted_text,
    stored_keyed_digest_key_id,
)

_startup_inventory_lock = Lock()
_startup_inventory_state = EncryptedDataStartupScan()
OPERATIONS_INVENTORY_ROW_LIMIT = 500
OPERATIONS_INVENTORY_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class OperationsEncryptedDataInventory:
    inventory: EncryptedDataInventoryResponse
    row_limit_per_category: int
    truncated_categories: tuple[str, ...]
    cache_hit: bool = False


@dataclass
class _InventoryScanBounds:
    row_limit: int
    truncated_categories: set[str]


@dataclass(frozen=True)
class _OperationsInventoryCacheEntry:
    key: tuple[object, ...]
    expires_at: float
    snapshot: OperationsEncryptedDataInventory


_operations_inventory_cache_lock = Lock()
_operations_inventory_cache: _OperationsInventoryCacheEntry | None = None


def scan_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings | None = None,
) -> EncryptedDataInventoryResponse:
    active_settings = settings or get_settings()
    return _scan_encrypted_data_inventory(db, settings=active_settings, bounds=None)


def get_operations_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings | None = None,
) -> OperationsEncryptedDataInventory:
    active_settings = settings or get_settings()
    active_key_id, previous_key_ids = configured_hashing_key_ids(active_settings)
    cache_key = (
        id(db.get_bind()),
        active_key_id,
        tuple(previous_key_ids),
        active_settings.app_data_encryption_key_was_derived,
        active_settings.require_explicit_data_encryption_key,
    )
    now = monotonic()
    global _operations_inventory_cache
    with _operations_inventory_cache_lock:
        cached = _operations_inventory_cache
        if cached is not None and cached.key == cache_key and cached.expires_at > now:
            return replace(cached.snapshot, cache_hit=True)

        bounds = _InventoryScanBounds(
            row_limit=OPERATIONS_INVENTORY_ROW_LIMIT,
            truncated_categories=set(),
        )
        inventory = _scan_encrypted_data_inventory(
            db,
            settings=active_settings,
            bounds=bounds,
        )
        snapshot = OperationsEncryptedDataInventory(
            inventory=inventory,
            row_limit_per_category=bounds.row_limit,
            truncated_categories=tuple(sorted(bounds.truncated_categories)),
        )
        _operations_inventory_cache = _OperationsInventoryCacheEntry(
            key=cache_key,
            expires_at=now + OPERATIONS_INVENTORY_CACHE_TTL_SECONDS,
            snapshot=snapshot,
        )
        return snapshot


def _clear_operations_encrypted_data_inventory_cache() -> None:
    global _operations_inventory_cache
    with _operations_inventory_cache_lock:
        _operations_inventory_cache = None


def _scan_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings,
    bounds: _InventoryScanBounds | None,
) -> EncryptedDataInventoryResponse:
    feeds = _scan_feeds(db, bounds=bounds)
    integration_secrets = _scan_integration_secrets(db, bounds=bounds)
    ai_provider_secrets = _scan_encrypted_text_column(
        db,
        AIProviderConfiguration.api_key_encrypted,
        category_name="ai_provider_secrets",
        order_columns=(AIProviderConfiguration.updated_at, AIProviderConfiguration.id),
        bounds=bounds,
        require_encrypted=True,
    )
    notification_webhooks = _scan_notification_webhooks(db, bounds=bounds)
    webhook_credential_secrets = _scan_webhook_credential_secrets(db, bounds=bounds)
    notification_delivery_snapshots = _scan_notification_delivery_snapshots(
        db, bounds=bounds
    )
    oidc_client_secrets = _scan_encrypted_text_column(
        db,
        OIDCProvider.client_secret_encrypted,
        category_name="oidc_client_secrets",
        order_columns=(OIDCProvider.updated_at, OIDCProvider.id),
        bounds=bounds,
    )
    mfa_secrets = _scan_encrypted_text_column(
        db,
        UserTOTPCredential.secret_encrypted,
        category_name="mfa_secrets",
        order_columns=(UserTOTPCredential.updated_at, UserTOTPCredential.id),
        bounds=bounds,
    )
    recovery_hashes = _scan_recovery_code_hashes(db, settings=settings, bounds=bounds)
    team_assessment_authorizations = _scan_team_assessment_authorizations(db, bounds=bounds)
    durable_ai_authorizations = _scan_durable_authorizations(db, models=(AIArticleContinuation, AIQualification),
        category_name="durable_ai_authorizations", bounds=bounds)
    publication_consumer_authorizations = _scan_durable_authorizations(db, models=(PublicationConsumer,),
        category_name="publication_consumer_authorizations", bounds=bounds)
    summary = _build_summary(
        feeds,
        integration_secrets,
        notification_webhooks,
        notification_delivery_snapshots,
        oidc_client_secrets,
        mfa_secrets,
        recovery_hashes,
        ai_provider_secrets,
        team_assessment_authorizations,
        webhook_credential_secrets,
        extra_categories=(durable_ai_authorizations, publication_consumer_authorizations),
    )

    warnings: list[str] = []
    if settings.app_data_encryption_key_was_derived:
        warnings.append(
            "APP_DATA_ENCRYPTION_KEY is using a derived development fallback. Set an explicit persistent value before relying on durable data."
        )
    if recovery_hashes.previous_key_codes:
        warnings.append(
            f"{recovery_hashes.previous_key_codes} unused MFA recovery codes still depend on a previous application data key. Regenerate those users' recovery codes before retiring that key."
        )
    if recovery_hashes.legacy_unversioned_codes:
        warnings.append(
            f"{recovery_hashes.legacy_unversioned_codes} unused MFA recovery codes use legacy unversioned hashes. Their key dependency cannot be proven; regenerate them before retiring any configured key."
        )
    if recovery_hashes.missing_key_codes:
        warnings.append(
            f"{recovery_hashes.missing_key_codes} unused MFA recovery codes reference a key that is not configured and cannot be verified."
        )

    status = _resolve_inventory_status(
        total_unreadable_fields=(
            summary.unreadable_fields + recovery_hashes.missing_key_codes
        ),
        warnings=warnings,
    )
    return EncryptedDataInventoryResponse(
        ok=(summary.unreadable_fields == 0 and recovery_hashes.missing_key_codes == 0),
        status=status,
        scanned_at=datetime.now(timezone.utc),
        warnings=warnings,
        require_explicit_app_data_encryption_key=settings.require_explicit_data_encryption_key,
        using_derived_app_data_encryption_key=settings.app_data_encryption_key_was_derived,
        key_retirement_blocked=recovery_hashes.key_retirement_blocked,
        startup_scan=get_startup_encrypted_data_inventory(),
        feeds=feeds,
        integration_secrets=integration_secrets,
        ai_provider_secrets=ai_provider_secrets,
        team_assessment_authorizations=team_assessment_authorizations,
        durable_ai_authorizations=durable_ai_authorizations,
        publication_consumer_authorizations=publication_consumer_authorizations,
        notification_webhooks=notification_webhooks,
        webhook_credential_secrets=webhook_credential_secrets,
        notification_delivery_snapshots=notification_delivery_snapshots,
        oidc_client_secrets=oidc_client_secrets,
        mfa_secrets=mfa_secrets,
        mfa_recovery_code_hashes=recovery_hashes,
        summary=summary,
    )


def refresh_startup_encrypted_data_inventory(
    db: Session,
    *,
    settings: Settings | None = None,
) -> EncryptedDataInventoryResponse:
    snapshot = scan_encrypted_data_inventory(db, settings=settings)
    _store_startup_inventory_success(snapshot)
    return snapshot


def get_startup_encrypted_data_inventory() -> EncryptedDataStartupScan:
    with _startup_inventory_lock:
        return _startup_inventory_state.model_copy(deep=True)


def record_startup_encrypted_data_inventory_error(error: str) -> None:
    with _startup_inventory_lock:
        _startup_inventory_state.completed_at = datetime.now(timezone.utc)
        _startup_inventory_state.status = "critical"
        _startup_inventory_state.error = error
        _startup_inventory_state.total_unreadable_records = None
        _startup_inventory_state.total_unreadable_fields = None
        _startup_inventory_state.key_retirement_blocked = None


def _store_startup_inventory_success(snapshot: EncryptedDataInventoryResponse) -> None:
    with _startup_inventory_lock:
        _startup_inventory_state.completed_at = snapshot.scanned_at
        _startup_inventory_state.status = snapshot.status
        _startup_inventory_state.error = None
        missing_hashes = snapshot.mfa_recovery_code_hashes.missing_key_codes
        _startup_inventory_state.total_unreadable_records = (
            snapshot.summary.unreadable_records + missing_hashes
        )
        _startup_inventory_state.total_unreadable_fields = (
            snapshot.summary.unreadable_fields + missing_hashes
        )
        _startup_inventory_state.key_retirement_blocked = (
            snapshot.key_retirement_blocked
        )


def _inventory_rows(
    db: Session,
    statement,
    *,
    category_name: str,
    order_columns: tuple,
    bounds: _InventoryScanBounds | None,
):
    if bounds is None:
        return db.execute(statement.execution_options(yield_per=OPERATIONS_INVENTORY_ROW_LIMIT))
    rows = db.execute(
        statement.order_by(*(column.desc() for column in order_columns)).limit(
            bounds.row_limit + 1
        )
    ).all()
    if len(rows) > bounds.row_limit:
        bounds.truncated_categories.add(category_name)
    return rows[: bounds.row_limit]


def _scan_recovery_code_hashes(
    db: Session,
    *,
    settings: Settings,
    bounds: _InventoryScanBounds | None = None,
) -> RecoveryCodeHashInventory:
    active_key_id, previous_key_ids = configured_hashing_key_ids(settings)
    previous = set(previous_key_ids)
    inventory = RecoveryCodeHashInventory()
    for (code_hash,) in _inventory_rows(
        db,
        select(UserRecoveryCode.code_hash).where(UserRecoveryCode.used_at.is_(None)),
        category_name="mfa_recovery_code_hashes",
        order_columns=(UserRecoveryCode.created_at, UserRecoveryCode.id),
        bounds=bounds,
    ):
        inventory.unused_codes += 1
        key_id = stored_keyed_digest_key_id(code_hash)
        if key_id is None:
            inventory.legacy_unversioned_codes += 1
        elif key_id == active_key_id:
            inventory.active_key_codes += 1
        elif key_id in previous:
            inventory.previous_key_codes += 1
        else:
            inventory.missing_key_codes += 1
    inventory.key_retirement_blocked = bool(
        inventory.previous_key_codes
        or inventory.legacy_unversioned_codes
        or inventory.missing_key_codes
    )
    return inventory


def _scan_feeds(
    db: Session, *, bounds: _InventoryScanBounds | None = None
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = _inventory_rows(
        db,
        select(Feed._url_encrypted),
        category_name="feeds",
        order_columns=(Feed.created_at, Feed.id),
        bounds=bounds,
    )
    for (encrypted_url,) in rows:
        category.total_records += 1
        if not is_encrypted_text(encrypted_url):
            continue
        category.encrypted_records += 1
        category.encrypted_fields += 1
        _plaintext, error = try_decrypt_feed_url(encrypted_url)
        if error:
            category.unreadable_records += 1
            category.unreadable_fields += 1
    return category


def _scan_integration_secrets(
    db: Session, *, bounds: _InventoryScanBounds | None = None
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = _inventory_rows(
        db,
        select(IntegrationInstance.secret_json),
        category_name="integration_secrets",
        order_columns=(IntegrationInstance.updated_at, IntegrationInstance.id),
        bounds=bounds,
    )
    for (secret_json,) in rows:
        category.total_records += 1
        encrypted_fields = _count_json_field(secret_json)
        unreadable_fields = _count_unreadable_json_field(secret_json)
        _apply_record_counts(
            category,
            encrypted_fields=encrypted_fields,
            unreadable_fields=unreadable_fields,
        )
    return category


def _scan_team_assessment_authorizations(
    db: Session, *, bounds: _InventoryScanBounds | None = None,
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    # One combined cap covers canonical jobs and archived result label snapshots.
    # Do not select private result JSON or team profiles into the health scanner.
    candidates = union_all(
        select(
            TeamItemAssessment.authorization_encrypted.label("authorization"),
            TeamItemAssessment.source_encrypted.label("source"),
            TeamItemAssessment.result_source_encrypted.label("result_source"),
            TeamItemAssessment.result_json.is_not(None).label("has_result"),
            literal(False).label("archived"),
            TeamItemAssessment.updated_at.label("updated_at"),
            TeamItemAssessment.id.label("id"),
            literal(0).label("version"),
        ),
        select(
            literal(None), literal(None), TeamAssessmentRevision.result_source_encrypted,
            literal(True), literal(True), TeamAssessmentRevision.created_at,
            TeamAssessmentRevision.assessment_id, TeamAssessmentRevision.version,
        ),
    ).subquery()
    rows = _inventory_rows(
        db,
        select(candidates.c.authorization, candidates.c.source, candidates.c.result_source, candidates.c.has_result, candidates.c.archived),
        category_name="team_assessment_authorizations",
        order_columns=(candidates.c.updated_at, candidates.c.id, candidates.c.version),
        bounds=bounds,
    )
    for authorization, source, result_source, has_result, archived in rows:
        category.total_records += 1
        values = (result_source,) if archived else (authorization, source)
        if not archived and (has_result or result_source is not None):
            values = (*values, result_source)
        _apply_record_counts(
            category,
            encrypted_fields=sum(_count_json_field(value) for value in values),
            unreadable_fields=sum(
                _count_unreadable_json_field(value) if is_encrypted_json(value) else 1
                for value in values
            ),
        )
    return category


def _scan_notification_webhooks(
    db: Session, *, bounds: _InventoryScanBounds | None = None
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = _inventory_rows(
        db,
        select(
            NotificationWebhook.url_template,
            NotificationWebhook.query_params_json,
            NotificationWebhook.headers_json,
            NotificationWebhook.body_fields_json,
            NotificationWebhook.body_template,
        ),
        category_name="notification_webhooks",
        order_columns=(NotificationWebhook.updated_at, NotificationWebhook.id),
        bounds=bounds,
    )
    for row in rows:
        category.total_records += 1
        encrypted_fields = 0
        unreadable_fields = 0
        encrypted_fields += _count_text_field(row.url_template)
        unreadable_fields += _count_unreadable_text_field(row.url_template)
        encrypted_fields += _count_json_field(row.query_params_json)
        unreadable_fields += _count_unreadable_json_field(row.query_params_json)
        encrypted_fields += _count_json_field(row.headers_json)
        unreadable_fields += _count_unreadable_json_field(row.headers_json)
        encrypted_fields += _count_json_field(row.body_fields_json)
        unreadable_fields += _count_unreadable_json_field(row.body_fields_json)
        encrypted_fields += _count_text_field(row.body_template)
        unreadable_fields += _count_unreadable_text_field(row.body_template)
        _apply_record_counts(
            category,
            encrypted_fields=encrypted_fields,
            unreadable_fields=unreadable_fields,
        )
    return category


def _scan_webhook_credential_secrets(
    db: Session, *, bounds: _InventoryScanBounds | None = None,
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = _inventory_rows(
        db, select(WebhookCredentialProfile.auth_secret_encrypted,
                   WebhookCredentialProfile.signing_secret_encrypted),
        category_name="webhook_credential_secrets",
        order_columns=(WebhookCredentialProfile.updated_at, WebhookCredentialProfile.id),
        bounds=bounds,
    )
    for row in rows:
        category.total_records += 1
        encrypted_fields = sum(is_encrypted_text(value) for value in row)
        unreadable_fields = 0
        for value in row:
            if value is None:
                continue
            if not is_encrypted_text(value):
                unreadable_fields += 1
                continue
            try:
                decrypt_text(value)
            except ValueError:
                unreadable_fields += 1
        _apply_record_counts(category, encrypted_fields=encrypted_fields,
                             unreadable_fields=unreadable_fields)
    return category


def _scan_notification_delivery_snapshots(
    db: Session, *, bounds: _InventoryScanBounds | None = None
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    rows = _inventory_rows(
        db,
        select(
            NotificationWebhookDelivery.rendered_url,
            NotificationWebhookDelivery.rendered_headers_json,
            NotificationWebhookDelivery.rendered_query_params_json,
            NotificationWebhookDelivery.rendered_body,
            NotificationWebhookDelivery.response_body_preview,
        ),
        category_name="notification_delivery_snapshots",
        order_columns=(
            NotificationWebhookDelivery.attempted_at,
            NotificationWebhookDelivery.id,
        ),
        bounds=bounds,
    )
    for row in rows:
        category.total_records += 1
        encrypted_fields = 0
        unreadable_fields = 0
        encrypted_fields += _count_text_field(row.rendered_url)
        unreadable_fields += _count_unreadable_text_field(row.rendered_url)
        encrypted_fields += _count_json_field(row.rendered_headers_json)
        unreadable_fields += _count_unreadable_json_field(row.rendered_headers_json)
        encrypted_fields += _count_json_field(row.rendered_query_params_json)
        unreadable_fields += _count_unreadable_json_field(
            row.rendered_query_params_json
        )
        encrypted_fields += _count_text_field(row.rendered_body)
        unreadable_fields += _count_unreadable_text_field(row.rendered_body)
        encrypted_fields += _count_text_field(row.response_body_preview)
        unreadable_fields += _count_unreadable_text_field(row.response_body_preview)
        _apply_record_counts(
            category,
            encrypted_fields=encrypted_fields,
            unreadable_fields=unreadable_fields,
        )
    return category


def _scan_encrypted_text_column(
    db: Session,
    column,
    *,
    category_name: str,
    order_columns: tuple,
    bounds: _InventoryScanBounds | None = None,
    require_encrypted: bool = False,
) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    for (value,) in _inventory_rows(
        db,
        select(column),
        category_name=category_name,
        order_columns=order_columns,
        bounds=bounds,
    ):
        category.total_records += 1
        if not is_encrypted_text(value):
            if require_encrypted and value is not None:
                category.unreadable_records += 1
                category.unreadable_fields += 1
            continue
        category.encrypted_records += 1
        category.encrypted_fields += 1
        try:
            decrypt_text(value)
        except ValueError:
            category.unreadable_records += 1
            category.unreadable_fields += 1
    return category


def _apply_record_counts(
    category: EncryptedDataInventoryCategory,
    *,
    encrypted_fields: int,
    unreadable_fields: int,
) -> None:
    category.encrypted_fields += encrypted_fields
    category.unreadable_fields += unreadable_fields
    if encrypted_fields > 0:
        category.encrypted_records += 1
    if unreadable_fields > 0:
        category.unreadable_records += 1


def _count_text_field(value: str | None) -> int:
    return 1 if is_encrypted_text(value) else 0


def _count_json_field(value) -> int:
    return 1 if is_encrypted_json(value) else 0


def _count_unreadable_text_field(value: str | None) -> int:
    if not is_encrypted_text(value):
        return 0
    try:
        decrypt_notification_text(value)
        return 0
    except ValueError:
        return 1


def _count_unreadable_json_field(value) -> int:
    if not is_encrypted_json(value):
        return 0
    try:
        decrypt_notification_json(value)
        return 0
    except ValueError:
        return 1


def _build_summary(
    feeds: EncryptedDataInventoryCategory,
    integration_secrets: EncryptedDataInventoryCategory,
    notification_webhooks: EncryptedDataInventoryCategory,
    notification_delivery_snapshots: EncryptedDataInventoryCategory,
    oidc_client_secrets: EncryptedDataInventoryCategory,
    mfa_secrets: EncryptedDataInventoryCategory,
    recovery_hashes: RecoveryCodeHashInventory,
    ai_provider_secrets: EncryptedDataInventoryCategory,
    team_assessment_authorizations: EncryptedDataInventoryCategory,
    webhook_credential_secrets: EncryptedDataInventoryCategory,
    extra_categories: tuple[EncryptedDataInventoryCategory, ...] = (),
) -> EncryptedDataInventorySummary:
    categories = (
        feeds,
        integration_secrets,
        notification_webhooks,
        notification_delivery_snapshots,
        oidc_client_secrets,
        mfa_secrets,
        ai_provider_secrets,
        team_assessment_authorizations,
        webhook_credential_secrets,
        *extra_categories,
    )
    return EncryptedDataInventorySummary(
        total_records=sum(category.total_records for category in categories),
        encrypted_records=sum(category.encrypted_records for category in categories),
        unreadable_records=sum(category.unreadable_records for category in categories),
        encrypted_fields=sum(category.encrypted_fields for category in categories),
        unreadable_fields=sum(category.unreadable_fields for category in categories),
    )


def _resolve_inventory_status(
    *, total_unreadable_fields: int, warnings: list[str]
) -> str:
    if total_unreadable_fields > 0:
        return "critical"
    if warnings:
        return "warning"
    return "healthy"


def _scan_durable_authorizations(db: Session, *, models: tuple, category_name: str,
                                 bounds: _InventoryScanBounds | None) -> EncryptedDataInventoryCategory:
    category = EncryptedDataInventoryCategory()
    candidates = union_all(*(select(model.authorization_encrypted.label("authorization"),
        model.created_at.label("created_at"), getattr(model, "run_id", None).label("id")
        if hasattr(model, "run_id") else model.id.label("id"), literal(index).label("kind"))
        for index, model in enumerate(models))).subquery()
    rows = _inventory_rows(db, select(candidates.c.authorization), category_name=category_name,
        order_columns=(candidates.c.created_at, candidates.c.id, candidates.c.kind), bounds=bounds)
    for (value,) in rows:
        category.total_records += 1
        _apply_record_counts(category, encrypted_fields=_count_json_field(value),
            unreadable_fields=_count_unreadable_json_field(value) if is_encrypted_json(value) else 1)
    return category
