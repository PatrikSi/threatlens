from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EncryptedDataInventoryStatus = Literal["healthy", "warning", "critical"]


class EncryptedDataInventoryCategory(BaseModel):
    total_records: int = Field(default=0, ge=0)
    encrypted_records: int = Field(default=0, ge=0)
    unreadable_records: int = Field(default=0, ge=0)
    encrypted_fields: int = Field(default=0, ge=0)
    unreadable_fields: int = Field(default=0, ge=0)


class RecoveryCodeHashInventory(BaseModel):
    unused_codes: int = Field(default=0, ge=0)
    active_key_codes: int = Field(default=0, ge=0)
    previous_key_codes: int = Field(default=0, ge=0)
    legacy_unversioned_codes: int = Field(default=0, ge=0)
    missing_key_codes: int = Field(default=0, ge=0)
    key_retirement_blocked: bool = False


class EncryptedDataInventorySummary(BaseModel):
    total_records: int = Field(default=0, ge=0)
    encrypted_records: int = Field(default=0, ge=0)
    unreadable_records: int = Field(default=0, ge=0)
    encrypted_fields: int = Field(default=0, ge=0)
    unreadable_fields: int = Field(default=0, ge=0)


class EncryptedDataStartupScan(BaseModel):
    completed_at: datetime | None = None
    status: EncryptedDataInventoryStatus | None = None
    error: str | None = None
    total_unreadable_records: int | None = Field(default=None, ge=0)
    total_unreadable_fields: int | None = Field(default=None, ge=0)
    key_retirement_blocked: bool | None = None


class EncryptedDataInventoryResponse(BaseModel):
    ok: bool
    status: EncryptedDataInventoryStatus
    scanned_at: datetime
    warnings: list[str] = Field(default_factory=list)
    require_explicit_app_data_encryption_key: bool
    using_derived_app_data_encryption_key: bool
    key_retirement_blocked: bool = False
    startup_scan: EncryptedDataStartupScan
    feeds: EncryptedDataInventoryCategory
    integration_secrets: EncryptedDataInventoryCategory
    notification_webhooks: EncryptedDataInventoryCategory
    notification_delivery_snapshots: EncryptedDataInventoryCategory
    oidc_client_secrets: EncryptedDataInventoryCategory = Field(
        default_factory=EncryptedDataInventoryCategory
    )
    mfa_secrets: EncryptedDataInventoryCategory = Field(
        default_factory=EncryptedDataInventoryCategory
    )
    mfa_recovery_code_hashes: RecoveryCodeHashInventory = Field(
        default_factory=RecoveryCodeHashInventory
    )
    summary: EncryptedDataInventorySummary
