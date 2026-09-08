"""Small contracts shared by durable export acceptance and execution."""

from __future__ import annotations

import uuid
from typing import Literal, NamedTuple, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict

from app.models.export_job import ExportJob
from app.models.service_account import ServiceAccount
from app.models.user import User

ExportPrincipal = User | ServiceAccount
ExportPrincipalType = Literal["user", "service_account"]
ExportTerminalStatus = Literal["failed", "cancelled", "expired"]
EXPORT_CHUNK_BYTES = 256 * 1024


class ExportAuthorizationSnapshot(BaseModel):
    """Immutable accepting scope; JSON remains compatible with existing jobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_kind: Literal["session_cookie", "api_token", "service_account_token"]
    credential_id: uuid.UUID
    permissions: frozenset[str]
    enforced: bool
    allowed_label_ids: frozenset[uuid.UUID]


class ExportSource(NamedTuple):
    item_id: uuid.UUID
    feed_id: uuid.UUID
    captured_label_id: uuid.UUID


class AcceptedExportJob(NamedTuple):
    job: ExportJob
    created: bool


class ExportCheckpoint(Protocol):
    def __call__(
        self, *, completed: int | None = None, force: bool = False
    ) -> None: ...


class ExportExecutionResult(TypedDict):
    status: Literal["skipped", "ready", "interrupted", "failed"]
    job_id: NotRequired[str]
