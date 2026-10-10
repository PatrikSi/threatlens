"""Authentication-independent contracts and bounds for MCP retrieval."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import uuid

from app.models.service_account import ServiceAccount
from app.models.user import User
from app.schemas.mcp_reads import MCPReadResult, SearchArticlesArguments
from app.services.authorization import AuthorizationContext
from app.services.data_access_policy import DataAccessContext
from app.services.export_job_contracts import ExportAuthorizationSnapshot

MAX_RESPONSE_BYTES = 65536
MAX_LINEAGE_SOURCES = 1000
MAX_STORED_ASSESSMENT_BYTES = 262144
MAX_STORED_EXTRACTION_BYTES = 262144


@dataclass(frozen=True)
class MCPReadContext:
    principal: User | ServiceAccount
    authorization: AuthorizationContext
    data_access: DataAccessContext
    credential_snapshot: ExportAuthorizationSnapshot
    cursor_secret: bytes


class MCPReadError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def encode_cursor(context, arguments, *, last_seen, last_id, snapshot_at) -> str:
    if len(context.cursor_secret) < 32:
        raise MCPReadError("mcp_unavailable", "Cursor signing is unavailable.")
    payload = json_bytes(
        {
            "v": 1,
            "binding": _cursor_binding(context, arguments),
            "last_seen": _utc(last_seen).isoformat(),
            "last_id": str(last_id),
            "snapshot_at": _utc(snapshot_at).isoformat(),
        }
    )
    signature = hmac.digest(context.cursor_secret, payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def decode_cursor(context, arguments) -> tuple[datetime, uuid.UUID, datetime] | None:
    if arguments.cursor is None:
        return None
    try:
        if len(context.cursor_secret) < 32:
            raise ValueError("signing unavailable")
        raw = base64.b64decode(
            arguments.cursor + "=" * (-len(arguments.cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.digest(context.cursor_secret, payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        value = json.loads(payload)
        if value["v"] != 1 or value["binding"] != _cursor_binding(context, arguments):
            raise ValueError("binding")
        last_seen = datetime.fromisoformat(value["last_seen"])
        snapshot = datetime.fromisoformat(value["snapshot_at"])
        now = datetime.now(timezone.utc)
        if snapshot.tzinfo is None or last_seen.tzinfo is None:
            raise ValueError("timezone")
        if snapshot > now + timedelta(seconds=30) or now - snapshot > timedelta(
            minutes=30
        ):
            raise ValueError("expired")
        return last_seen, uuid.UUID(value["last_id"]), snapshot
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise MCPReadError(
            "invalid_cursor",
            "The search cursor is invalid or expired. Start a new search.",
        ) from exc


def _cursor_binding(context: MCPReadContext, arguments: SearchArticlesArguments) -> str:
    return hashlib.sha256(
        json_bytes(
            {
                "principal_type": context.authorization.principal_type,
                "principal_id": str(context.principal.id),
                "credential_id": str(context.credential_snapshot.credential_id),
                "authorization_revision": context.authorization.policy_revision,
                "data_revision": context.data_access.policy_revision,
                "filters": arguments.model_dump(mode="json", exclude={"cursor"}),
            }
        )
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def bounded_result(result: MCPReadResult, *, max_bytes: int) -> dict:
    """Bound the actual JSON bytes, preserving metadata and signaling omissions."""
    if not 2048 <= max_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("MCP response budget must be between 2048 and 65536 bytes")
    value = result.model_dump(mode="json")
    value["truncation"]["fields"] = _compact_paths(value["truncation"]["fields"])
    while len(json_bytes(value)) > max_bytes:
        candidates = list(_shrinkable(value["data"], "data"))
        if not candidates:
            raise MCPReadError(
                "response_too_large",
                "The result cannot fit the response limit. Use the canonical record link.",
            )
        _, parent, key, path = max(candidates, key=lambda entry: entry[0])
        entry = parent[key]
        if key == "structured_extraction" and isinstance(entry, dict):
            # Entities, relationships and verified quote offsets form one unit.
            parent[key] = None
            parent["structured_extraction_omitted"] = True
            parent["structured_extraction_omission_reason"] = "response_byte_limit"
            parent["primary_source_fallback"] = True
        else:
            parent[key] = entry[: max(0, len(entry) // 2)]
        truncated = value["truncation"]
        truncated["truncated"] = True
        truncated["fields"] = _compact_paths([*truncated["fields"], path])
        if "response_byte_limit" not in truncated["reasons"]:
            truncated["reasons"].append("response_byte_limit")
    return value


def _compact_paths(paths: list[str]) -> list[str]:
    """Preserve the affected collection when detailed paths crowd out its data."""
    paths = sorted(set(paths))
    if len(paths) > 32:
        paths = sorted({".".join(path.split(".")[:2]) for path in paths})
    compacted = []
    for path in paths:
        if not any(path.startswith(parent + ".") for parent in compacted):
            compacted.append(path)
    return compacted


def _shrinkable(value, path):
    entries = value.items() if isinstance(value, dict) else enumerate(value)
    for key, child in entries:
        child_path = f"{path}.{key}"
        if key == "structured_extraction" and isinstance(child, dict):
            yield len(json_bytes(child)), value, key, child_path
            continue
        if isinstance(child, str) and len(child) > 256:
            yield len(json_bytes(child)), value, key, child_path
        elif isinstance(child, list) and len(child) > 1:
            yield len(json_bytes(child)), value, key, child_path
        if isinstance(child, (dict, list)):
            yield from _shrinkable(child, child_path)


def encode_bound_cursor(context: MCPReadContext, arguments, position: dict) -> str:
    """Sign opaque evidence positions with the same credential/policy binding."""
    if len(context.cursor_secret) < 32:
        raise MCPReadError("mcp_unavailable", "Cursor signing is unavailable.")
    payload = json_bytes(
        {
            "v": 1,
            "binding": _cursor_binding(context, arguments),
            "issued": datetime.now(timezone.utc).isoformat(),
            "position": position,
        }
    )
    return (
        base64.urlsafe_b64encode(
            payload + hmac.digest(context.cursor_secret, payload, "sha256")
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def decode_bound_cursor(context: MCPReadContext, arguments) -> dict | None:
    if arguments.cursor is None:
        return None
    try:
        raw = base64.b64decode(
            arguments.cursor + "=" * (-len(arguments.cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload, signature = raw[:-32], raw[-32:]
        if len(context.cursor_secret) < 32 or not hmac.compare_digest(
            signature, hmac.digest(context.cursor_secret, payload, "sha256")
        ):
            raise ValueError("signature")
        value = json.loads(payload)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(value["issued"])
        if (
            value["v"] != 1
            or value["binding"] != _cursor_binding(context, arguments)
            or not timedelta(seconds=-30) <= age <= timedelta(minutes=30)
        ):
            raise ValueError("binding or expiry")
        if not isinstance(value["position"], dict):
            raise ValueError("position")
        return value["position"]
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise MCPReadError(
            "invalid_cursor",
            "The evidence cursor is invalid or expired. Start from the first page.",
        ) from exc
