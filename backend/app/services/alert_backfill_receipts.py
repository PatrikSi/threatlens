from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)


ALERT_BACKFILL_APPLY_RESULT_KEY = "_threatlens_apply_result"
ALERT_BACKFILL_APPLY_RESULT_VERSION = 3


@dataclass(frozen=True)
class AlertBackfillCandidate:
    item_id: uuid.UUID
    content_hash: str
    title: str
    first_seen_at: datetime


@dataclass(frozen=True)
class AlertBackfillPersistenceResult:
    request_ids: tuple[uuid.UUID, ...]
    existing_count: int
    skipped_count: int
    next_cursor_first_seen_at: datetime | None
    next_cursor_item_id: uuid.UUID | None
    replayed: bool = False
    enqueue_failed: bool = False


class AlertBackfillPreviewError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def load_alert_backfill_apply_result(
    db: Session,
    preview: AlertBackfillPreview,
) -> AlertBackfillPersistenceResult | None:
    entries = preview.candidates_json
    if not isinstance(entries, list):
        raise _invalid_alert_backfill_result()
    envelope_indexes = [
        index
        for index, entry in enumerate(entries)
        if isinstance(entry, dict) and ALERT_BACKFILL_APPLY_RESULT_KEY in entry
    ]
    if not envelope_indexes:
        return None
    if envelope_indexes != [len(entries) - 1]:
        raise _invalid_alert_backfill_result()
    envelope = entries[-1].get(ALERT_BACKFILL_APPLY_RESULT_KEY)
    if not isinstance(envelope, dict):
        raise _invalid_alert_backfill_result()
    candidates = parse_alert_backfill_candidates(preview, entries=entries[:-1])

    version = envelope.get("version")
    if type(version) is not int:
        raise _invalid_alert_backfill_result()
    if version == ALERT_BACKFILL_APPLY_RESULT_VERSION:
        return _load_v3_alert_backfill_apply_result(
            db,
            preview=preview,
            candidates=candidates,
            envelope=envelope,
        )
    if version not in {1, 2}:
        raise AlertBackfillPreviewError(
            "This alert backfill result was written by an unsupported ThreatLens version. Upgrade ThreatLens before retrying it.",
            code="alert_backfill_apply_result_unsupported",
        )
    return _load_legacy_alert_backfill_apply_result(
        db,
        preview=preview,
        candidates=candidates,
        envelope=envelope,
        version=version,
    )


def parse_alert_backfill_candidates(
    preview: AlertBackfillPreview,
    *,
    entries: object | None = None,
) -> tuple[AlertBackfillCandidate, ...]:
    raw_entries = preview.candidates_json if entries is None else entries
    if (
        not isinstance(raw_entries, list)
        or type(preview.item_limit) is not int
        or not 1 <= preview.item_limit <= 500
        or len(raw_entries) > preview.item_limit
        or type(preview.matched_count) is not int
        or preview.matched_count < 0
        or type(preview.has_more) is not bool
    ):
        raise AlertBackfillPreviewError(
            "The persisted alert backfill preview has an invalid candidate list. Recalculate it before applying.",
            code="alert_backfill_preview_invalid",
        )
    candidates: list[AlertBackfillCandidate] = []
    seen_item_ids: set[uuid.UUID] = set()
    cursor_pair_present = (
        preview.cursor_first_seen_at is not None and preview.cursor_item_id is not None
    )
    if (preview.cursor_first_seen_at is None) != (preview.cursor_item_id is None):
        raise _invalid_alert_backfill_preview_metadata()
    since = _as_utc(preview.since)
    until = _as_utc(preview.until)
    if since > until:
        raise _invalid_alert_backfill_preview_metadata()
    cursor_position = (
        (
            _as_utc(preview.cursor_first_seen_at),
            preview.cursor_item_id.int,
        )
        if cursor_pair_present
        else None
    )
    if cursor_position is not None and not since <= cursor_position[0] <= until:
        raise _invalid_alert_backfill_preview_metadata()
    previous_position: tuple[datetime, int] | None = None
    try:
        for entry in raw_entries:
            if not isinstance(entry, dict) or set(entry) != {
                "item_id",
                "content_hash",
                "title",
                "first_seen_at",
            }:
                raise TypeError("candidate must use the expected object shape")
            if not isinstance(entry["item_id"], str):
                raise TypeError("candidate item_id must be a string")
            item_id = uuid.UUID(entry["item_id"])
            content_hash = entry["content_hash"]
            title = entry["title"]
            if not isinstance(entry["first_seen_at"], str):
                raise TypeError("candidate first_seen_at must be a string")
            first_seen_at = datetime.fromisoformat(entry["first_seen_at"])
            normalized_first_seen_at = _as_utc(first_seen_at)
            position = (normalized_first_seen_at, item_id.int)
            if (
                item_id in seen_item_ids
                or not isinstance(content_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
                or not isinstance(title, str)
                or len(title) > 512
                or first_seen_at.tzinfo is None
                or not since <= normalized_first_seen_at <= until
                or (previous_position is not None and position <= previous_position)
                or (cursor_position is not None and position <= cursor_position)
            ):
                raise ValueError("candidate fields are invalid")
            seen_item_ids.add(item_id)
            previous_position = position
            candidates.append(
                AlertBackfillCandidate(
                    item_id,
                    content_hash,
                    title,
                    normalized_first_seen_at,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise AlertBackfillPreviewError(
            "The persisted alert backfill preview has malformed candidates. Recalculate it before applying.",
            code="alert_backfill_preview_invalid",
        ) from exc

    cursor_present = (
        preview.next_cursor_first_seen_at is not None
        and preview.next_cursor_item_id is not None
    )
    if (
        (
            preview.has_more
            and (
                len(candidates) != preview.item_limit
                or preview.matched_count <= len(candidates)
            )
        )
        or (not preview.has_more and len(candidates) != preview.matched_count)
        or bool(preview.has_more) != cursor_present
        or (
            preview.has_more
            and (
                not candidates
                or candidates[-1].item_id != preview.next_cursor_item_id
                or _as_utc(candidates[-1].first_seen_at)
                != _as_utc(preview.next_cursor_first_seen_at)
            )
        )
    ):
        raise _invalid_alert_backfill_preview_metadata()
    return tuple(candidates)


def alert_backfill_preview_fingerprint(
    preview: AlertBackfillPreview,
    candidates: tuple[AlertBackfillCandidate, ...],
) -> str:
    payload = {
        "preview_id": str(preview.id),
        "actor_user_id": str(preview.actor_user_id),
        "since": _as_utc(preview.since).isoformat(),
        "until": _as_utc(preview.until).isoformat(),
        "item_limit": preview.item_limit,
        "cursor_first_seen_at": _optional_datetime_text(preview.cursor_first_seen_at),
        "cursor_item_id": _optional_uuid_text(preview.cursor_item_id),
        "matched_count": preview.matched_count,
        "has_more": preview.has_more,
        "next_cursor_first_seen_at": _optional_datetime_text(
            preview.next_cursor_first_seen_at
        ),
        "next_cursor_item_id": _optional_uuid_text(preview.next_cursor_item_id),
        "candidates": [
            {
                "item_id": str(candidate.item_id),
                "content_hash": candidate.content_hash,
                "title": candidate.title,
                "first_seen_at": _as_utc(candidate.first_seen_at).isoformat(),
            }
            for candidate in candidates
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def alert_backfill_response_payload(
    preview: AlertBackfillPreview,
    result: AlertBackfillPersistenceResult,
) -> dict[str, object]:
    return {
        "accepted": len(result.request_ids),
        "existing": result.existing_count,
        "skipped": result.skipped_count,
        "enqueue_failed": False,
        "has_more": preview.next_cursor_item_id is not None,
        "next_cursor_first_seen_at": _optional_datetime_text(
            preview.next_cursor_first_seen_at
        ),
        "next_cursor_item_id": _optional_uuid_text(preview.next_cursor_item_id),
        "notifications_enabled": False,
    }


def _load_v3_alert_backfill_apply_result(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    candidates: tuple[AlertBackfillCandidate, ...],
    envelope: dict,
) -> AlertBackfillPersistenceResult:
    if set(envelope) != {"version", "preview_fingerprint", "outcomes", "response"}:
        raise _invalid_alert_backfill_result()
    if envelope["preview_fingerprint"] != alert_backfill_preview_fingerprint(
        preview, candidates
    ):
        raise _invalid_alert_backfill_result()
    outcomes = envelope["outcomes"]
    if not isinstance(outcomes, list) or len(outcomes) != len(candidates):
        raise _invalid_alert_backfill_result()

    accepted: list[dict[str, object]] = []
    existing_count = 0
    skipped_count = 0
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        if not isinstance(outcome, dict):
            raise _invalid_alert_backfill_result()
        if (
            outcome.get("item_id") != str(candidate.item_id)
            or outcome.get("content_hash") != candidate.content_hash
        ):
            raise _invalid_alert_backfill_result()
        status = outcome.get("status")
        if status == "accepted":
            if set(outcome) != {
                "item_id",
                "content_hash",
                "status",
                "request_id",
                "request_version",
                "backfill_count",
                "accepted_at",
                "activity_id",
            }:
                raise _invalid_alert_backfill_result()
            _validate_v3_accepted_outcome(outcome, preview)
            accepted.append(outcome)
        elif status in {"existing", "skipped"}:
            if set(outcome) != {"item_id", "content_hash", "status"}:
                raise _invalid_alert_backfill_result()
            if status == "existing":
                existing_count += 1
            else:
                skipped_count += 1
        else:
            raise _invalid_alert_backfill_result()

    request_ids = tuple(uuid.UUID(outcome["request_id"]) for outcome in accepted)
    if len(request_ids) != len(set(request_ids)):
        raise _invalid_alert_backfill_result()
    result = AlertBackfillPersistenceResult(
        request_ids,
        existing_count,
        skipped_count,
        preview.next_cursor_first_seen_at,
        preview.next_cursor_item_id,
        replayed=True,
    )
    if envelope["response"] != alert_backfill_response_payload(preview, result):
        raise _invalid_alert_backfill_result()
    _validate_v3_alert_backfill_requests(db, preview=preview, accepted=accepted)
    return result


def _load_legacy_alert_backfill_apply_result(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    candidates: tuple[AlertBackfillCandidate, ...],
    envelope: dict,
    version: int,
) -> AlertBackfillPersistenceResult:
    expected_keys = {"version", "request_ids", "existing_count", "skipped_count"}
    if version == 2:
        expected_keys |= {
            "candidate_fingerprint",
            "requests",
            "dispatch_state",
        }
    if set(envelope) != expected_keys:
        raise _invalid_alert_backfill_result()
    raw_request_ids = envelope.get("request_ids")
    existing_count = envelope.get("existing_count")
    skipped_count = envelope.get("skipped_count")
    if (
        not isinstance(raw_request_ids, list)
        or type(existing_count) is not int
        or type(skipped_count) is not int
    ):
        raise _invalid_alert_backfill_result()
    try:
        request_ids = tuple(
            uuid.UUID(value) if isinstance(value, str) else None
            for value in raw_request_ids
        )
    except ValueError as exc:
        raise _invalid_alert_backfill_result() from exc
    if (
        None in request_ids
        or len(request_ids) > len(candidates)
        or len(set(request_ids)) != len(request_ids)
        or existing_count < 0
        or skipped_count < 0
        or len(request_ids) + existing_count + skipped_count != len(candidates)
    ):
        raise _invalid_alert_backfill_result()
    exact_bindings = None
    if version == 2:
        exact_bindings = _validate_v2_alert_backfill_envelope(
            envelope, candidates, request_ids
        )
    _validate_legacy_alert_backfill_requests(
        db,
        preview=preview,
        candidates=candidates,
        request_ids=request_ids,
        exact_bindings=exact_bindings,
    )
    return AlertBackfillPersistenceResult(
        request_ids,
        existing_count,
        skipped_count,
        preview.next_cursor_first_seen_at,
        preview.next_cursor_item_id,
        replayed=True,
        enqueue_failed=version == 2 and envelope["dispatch_state"] != "published",
    )


def _validate_v3_accepted_outcome(
    outcome: dict,
    preview: AlertBackfillPreview,
) -> None:
    request_version = outcome["request_version"]
    backfill_count = outcome["backfill_count"]
    accepted_at = outcome["accepted_at"]
    if (
        not isinstance(outcome["request_id"], str)
        or type(request_version) is not int
        or request_version < 1
        or type(backfill_count) is not int
        or backfill_count < 1
        or not isinstance(accepted_at, str)
        or not isinstance(outcome["activity_id"], str)
    ):
        raise _invalid_alert_backfill_result()
    try:
        uuid.UUID(outcome["request_id"])
        uuid.UUID(outcome["activity_id"])
        parsed_accepted_at = datetime.fromisoformat(accepted_at)
    except ValueError as exc:
        raise _invalid_alert_backfill_result() from exc
    if (
        parsed_accepted_at.tzinfo is None
        or preview.consumed_at is None
        or _as_utc(parsed_accepted_at) > _as_utc(preview.consumed_at)
    ):
        raise _invalid_alert_backfill_result()


def _validate_v3_alert_backfill_requests(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    accepted: list[dict[str, object]],
) -> None:
    if not accepted:
        return
    snapshots = {uuid.UUID(outcome["request_id"]): outcome for outcome in accepted}
    rows = list(
        db.scalars(
            select(AlertEvaluationRequest).where(
                AlertEvaluationRequest.id.in_(snapshots)
            )
        ).all()
    )
    if len(rows) != len(snapshots):
        raise _invalid_alert_backfill_result()
    for row in rows:
        snapshot = snapshots[row.id]
        if (
            str(row.item_id) != snapshot["item_id"]
            or row.item_content_hash != snapshot["content_hash"]
            or row.version < snapshot["request_version"]
            or row.backfill_count < snapshot["backfill_count"]
        ):
            raise _invalid_alert_backfill_result()
    _validate_v3_alert_backfill_activities(db, preview=preview, snapshots=snapshots)


def _validate_v3_alert_backfill_activities(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    snapshots: dict[uuid.UUID, dict[str, object]],
) -> None:
    activity_ids = {
        uuid.UUID(snapshot["activity_id"]) for snapshot in snapshots.values()
    }
    if len(activity_ids) != len(snapshots):
        raise _invalid_alert_backfill_result()
    activities = list(
        db.scalars(
            select(AlertEvaluationRequestActivity).where(
                AlertEvaluationRequestActivity.id.in_(activity_ids)
            )
        ).all()
    )
    if len(activities) != len(activity_ids):
        raise _invalid_alert_backfill_result()
    activities_by_id = {activity.id: activity for activity in activities}
    for request_id, snapshot in snapshots.items():
        activity = activities_by_id.get(uuid.UUID(snapshot["activity_id"]))
        details = (
            activity.details_json
            if activity is not None and isinstance(activity.details_json, dict)
            else {}
        )
        action_is_valid = activity is not None and (
            (activity.action == "accepted" and details.get("source") == "backfill")
            or activity.action == "backfill_requested"
        )
        if (
            activity is None
            or activity.request_id != request_id
            or activity.actor_user_id != preview.actor_user_id
            or not action_is_valid
            or details.get("backfill_preview_id") != str(preview.id)
            or details.get("request_version") != snapshot["request_version"]
            or details.get("backfill_count") != snapshot["backfill_count"]
            or details.get("accepted_at") != snapshot["accepted_at"]
            or details.get("notify") is not False
            or details.get("respect_rule_cutover") is not False
        ):
            raise _invalid_alert_backfill_result()


def _validate_alert_backfill_activities(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    snapshots: dict[uuid.UUID, dict[str, object]],
) -> None:
    activities = db.scalars(
        select(AlertEvaluationRequestActivity).where(
            AlertEvaluationRequestActivity.request_id.in_(snapshots),
            AlertEvaluationRequestActivity.action.in_(
                ["accepted", "backfill_requested"]
            ),
        )
    ).all()
    proven: set[uuid.UUID] = set()
    for activity in activities:
        snapshot = snapshots[activity.request_id]
        expected_count = snapshot.get("backfill_count")
        details = (
            activity.details_json if isinstance(activity.details_json, dict) else {}
        )
        non_notifying = (
            details.get("notify") is False
            and details.get("respect_rule_cutover") is False
        )
        if activity.action == "accepted":
            matches = (
                expected_count in {None, 1}
                and details.get("source") == "backfill"
                and non_notifying
            )
        else:
            matches = (
                activity.actor_user_id == preview.actor_user_id
                and (
                    expected_count is None
                    or details.get("backfill_count") == expected_count
                )
                and non_notifying
            )
        if matches:
            proven.add(activity.request_id)
    if proven != set(snapshots):
        raise _invalid_alert_backfill_result()


def _validate_v2_alert_backfill_envelope(
    envelope: dict,
    candidates: tuple[AlertBackfillCandidate, ...],
    request_ids: tuple[uuid.UUID, ...],
) -> set[tuple[uuid.UUID, uuid.UUID, str]]:
    requests = envelope.get("requests")
    if (
        not isinstance(requests, list)
        or envelope.get("candidate_fingerprint")
        != _legacy_alert_backfill_candidate_fingerprint(candidates)
        or envelope.get("dispatch_state") not in {"pending", "published", "deferred"}
        or len(requests) != len(request_ids)
    ):
        raise _invalid_alert_backfill_result()
    normalized: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for binding in requests:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"request_id", "item_id", "content_hash", "notify"}
            or binding["notify"] is not False
            or not isinstance(binding["request_id"], str)
            or not isinstance(binding["item_id"], str)
            or not isinstance(binding["content_hash"], str)
        ):
            raise _invalid_alert_backfill_result()
        try:
            normalized.add(
                (
                    uuid.UUID(binding["request_id"]),
                    uuid.UUID(binding["item_id"]),
                    binding["content_hash"],
                )
            )
        except ValueError as exc:
            raise _invalid_alert_backfill_result() from exc
    candidate_pairs = {
        (candidate.item_id, candidate.content_hash) for candidate in candidates
    }
    if {request_id for request_id, _, _ in normalized} != set(request_ids) or any(
        (item_id, content_hash) not in candidate_pairs
        for _, item_id, content_hash in normalized
    ):
        raise _invalid_alert_backfill_result()
    return normalized


def _validate_legacy_alert_backfill_requests(
    db: Session,
    *,
    preview: AlertBackfillPreview,
    candidates: tuple[AlertBackfillCandidate, ...],
    request_ids: tuple[uuid.UUID, ...],
    exact_bindings: set[tuple[uuid.UUID, uuid.UUID, str]] | None = None,
) -> None:
    if not request_ids:
        return
    rows = list(
        db.scalars(
            select(AlertEvaluationRequest).where(
                AlertEvaluationRequest.id.in_(request_ids)
            )
        ).all()
    )
    candidate_pairs = {
        (candidate.item_id, candidate.content_hash) for candidate in candidates
    }
    row_bindings = {(row.id, row.item_id, row.item_content_hash) for row in rows}
    if (
        len(rows) != len(request_ids)
        or any(
            (row.item_id, row.item_content_hash) not in candidate_pairs for row in rows
        )
        or (exact_bindings is not None and row_bindings != exact_bindings)
    ):
        raise _invalid_alert_backfill_result()
    snapshots = {row.id: {"backfill_count": None} for row in rows}
    _validate_alert_backfill_activities(db, preview=preview, snapshots=snapshots)


def _legacy_alert_backfill_candidate_fingerprint(
    candidates: tuple[AlertBackfillCandidate, ...],
) -> str:
    payload = [
        {"item_id": str(candidate.item_id), "content_hash": candidate.content_hash}
        for candidate in candidates
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _invalid_alert_backfill_result() -> AlertBackfillPreviewError:
    return AlertBackfillPreviewError(
        "The stored alert backfill result is invalid and cannot be replayed safely. Create a new preview before retrying.",
        code="alert_backfill_apply_result_invalid",
    )


def _invalid_alert_backfill_preview_metadata() -> AlertBackfillPreviewError:
    return AlertBackfillPreviewError(
        "The persisted alert backfill preview metadata is inconsistent. Recalculate it before applying.",
        code="alert_backfill_preview_invalid",
    )


def _optional_datetime_text(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _optional_uuid_text(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
