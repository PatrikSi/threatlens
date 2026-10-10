"""Stable semantic approval identity; annotations do not alter hunt content."""

import hashlib
import json


def hunt_approval_fingerprint(row, hunt: dict) -> str:
    content = {
        key: value
        for key, value in hunt.items()
        if key
        not in {
            "review_status",
            "review_note",
            "reviewed_by_user_id",
            "reviewed_at",
            "investigation_id",
            "approval_id",
        }
    }
    value = {
        "hunt": content,
        "context_version": row.result_context_version,
        "source_revision": row.result_source_version,
        "article_id": str(row.result_article_id) if row.result_article_id else None,
        "article_retrieved_at": row.result_article_retrieved_at.isoformat()
        if row.result_article_retrieved_at
        else None,
    }
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
