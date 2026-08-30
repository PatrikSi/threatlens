import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_principal_type: str | None
    actor_principal_id: uuid.UUID | None
    credential_kind: str | None
    credential_id: uuid.UUID | None
    request_id: str | None
    source_ip: str | None
    authorization_elevation_ids: list[uuid.UUID]
    action: str
    resource_type: str
    resource_id: str | None
    success: bool
    metadata_json: dict
    created_at: datetime


class AuditLogListResponse(BaseModel):
    logs: list[AuditLogResponse]
    total: int
    page: int
    page_size: int


class AuditLogExportResponse(BaseModel):
    exported_at: datetime
    total: int
    truncated: bool
    logs: list[AuditLogResponse]
