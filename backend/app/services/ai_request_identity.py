"""Stable provider request identities, independent of attempt settlement."""

from __future__ import annotations

import hashlib
import json
import uuid

from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_protocol import provider_capability_snapshot


def ai_request_fingerprint(
    *,
    active: ActiveAISettings,
    feature_type: str,
    messages: list[dict[str, str]],
    item_id: uuid.UUID | None,
    daily_brief_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
    requested_max_tokens: int,
) -> str:
    profile_identity = {}
    if getattr(active, "provider_id", None) is not None:
        profile_identity = {
            "provider_id": str(active.provider_id),
            "provider_version": active.provider_version,
        }
    serialized = json.dumps(
        {
            "feature_type": feature_type,
            "messages": messages,
            "item_id": str(item_id) if item_id is not None else None,
            "daily_brief_id": (
                str(daily_brief_id) if daily_brief_id is not None else None
            ),
            "report_id": str(report_id) if report_id is not None else None,
            "provider_type": active.provider_type,
            **profile_identity,
            **provider_capability_snapshot(active),
            "base_url": getattr(active, "base_url", None),
            "model": active.model,
            "temperature": getattr(active, "temperature", None),
            "max_tokens": max(1, int(requested_max_tokens)),
            "stream": False,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
