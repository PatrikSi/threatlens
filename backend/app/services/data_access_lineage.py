from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
)
from app.models.feed import Feed
from app.services.data_access_policy import DataPolicyUnavailable


_SOURCE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SOURCE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID_MAX_LENGTH = 512
_SOURCE_VERSION_MAX_LENGTH = 128


def normalize_sources(sources, *, current_revision: int):
    from app.services.data_access_envelopes import (
        DataAccessEnvelopeConflict,
        DataAccessSourceInput,
    )

    if not sources:
        raise DataAccessEnvelopeConflict(
            "Every normalized data access envelope requires at least one source."
        )
    normalized_by_identity: dict[
        tuple[str, str, str, uuid.UUID | None], DataAccessSourceInput
    ] = {}
    for source in sources:
        if not isinstance(source, DataAccessSourceInput):
            raise DataAccessEnvelopeConflict(
                "Normalized data access sources require DataAccessSourceInput values."
            )
        source_type = source.source_type.strip().lower()
        if not _SOURCE_TYPE_PATTERN.fullmatch(source_type):
            raise DataAccessEnvelopeConflict(
                "Data access source types must be normalized lowercase identifiers."
            )
        source_id = _normalize_source_text(
            source.source_id,
            field_name="source_id",
            max_length=_SOURCE_ID_MAX_LENGTH,
        )
        source_version = _normalize_source_text(
            source.source_version,
            field_name="source_version",
            max_length=_SOURCE_VERSION_MAX_LENGTH,
        )
        if not isinstance(source.handling_label_id, uuid.UUID):
            raise DataAccessEnvelopeConflict(
                "Data access sources require UUID handling-label identifiers."
            )
        if source.source_feed_id is not None and not isinstance(
            source.source_feed_id, uuid.UUID
        ):
            raise DataAccessEnvelopeConflict(
                "Data access sources require UUID feed identifiers."
            )
        if source.source_parent_id is not None and not isinstance(
            source.source_parent_id, uuid.UUID
        ):
            raise DataAccessEnvelopeConflict(
                "Data access sources require UUID parent-source identifiers."
            )
        _validate_captured_policy_revision(
            source.captured_policy_revision,
            current_revision,
        )
        source_digest = None
        if source.source_digest is not None:
            if not isinstance(source.source_digest, str):
                raise DataAccessEnvelopeConflict(
                    "Data access source digests must be hexadecimal SHA-256 values."
                )
            source_digest = source.source_digest.strip().lower()
            if not _SOURCE_DIGEST_PATTERN.fullmatch(source_digest):
                raise DataAccessEnvelopeConflict(
                    "Data access source digests must be 64-character hexadecimal SHA-256 values."
                )
        captured_at = source.captured_at
        if captured_at is not None:
            if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
                raise DataAccessEnvelopeConflict(
                    "Data access source capture times must include a timezone."
                )
            captured_at = captured_at.astimezone(timezone.utc)
        normalized = DataAccessSourceInput(
            source_type=source_type,
            source_id=source_id,
            source_version=source_version,
            source_feed_id=source.source_feed_id,
            source_parent_id=source.source_parent_id,
            handling_label_id=source.handling_label_id,
            captured_policy_revision=source.captured_policy_revision,
            source_digest=source_digest,
            captured_at=captured_at,
        )
        identity = source_identity(normalized)
        previous = normalized_by_identity.get(identity)
        if previous is not None and previous != normalized:
            raise DataAccessEnvelopeConflict(
                "Conflicting data access sources reuse the same immutable identity.",
                context={
                    "source_type": source_type,
                    "source_id": source_id,
                    "source_version": source_version,
                },
            )
        normalized_by_identity[identity] = normalized
    return tuple(
        normalized_by_identity[identity]
        for identity in sorted(
            normalized_by_identity,
            key=lambda value: (*value[:3], str(value[3] or "")),
        )
    )


def validate_source_references(
    db: Session,
    *,
    envelope_id: uuid.UUID | None,
    sources: Sequence,
) -> None:
    from app.services.data_access_envelopes import DataAccessEnvelopeConflict

    feed_ids = {source.source_feed_id for source in sources if source.source_feed_id}
    feeds = list(
        db.scalars(
            select(Feed)
            .where(Feed.id.in_(feed_ids))
            .order_by(Feed.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    feeds_by_id = {feed.id: feed for feed in feeds}
    missing_feed_ids = sorted(feed_ids - set(feeds_by_id), key=str)
    if missing_feed_ids:
        raise DataAccessEnvelopeConflict(
            "One or more feeds referenced by data access lineage do not exist.",
            context={"feed_ids": [str(value) for value in missing_feed_ids]},
        )
    for source in sources:
        if source.source_feed_id is None or source.source_parent_id is not None:
            continue
        feed = feeds_by_id[source.source_feed_id]
        if source.handling_label_id != feed.handling_label_id:
            raise DataAccessEnvelopeConflict(
                "Feed lineage must use the feed's current handling label.",
                context={"feed_id": str(feed.id)},
            )

    parent_ids = {
        source.source_parent_id
        for source in sources
        if source.source_parent_id is not None
    }
    parent_rows = list(
        db.scalars(
            select(DataAccessEnvelopeSource)
            .where(DataAccessEnvelopeSource.id.in_(parent_ids))
            .order_by(
                DataAccessEnvelopeSource.envelope_id,
                DataAccessEnvelopeSource.source_type,
                DataAccessEnvelopeSource.source_id,
                DataAccessEnvelopeSource.source_version,
                DataAccessEnvelopeSource.id,
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    parents_by_id = {row.id: row for row in parent_rows}
    for source in sources:
        if source.source_parent_id is None:
            continue
        parent = parents_by_id.get(source.source_parent_id)
        if parent is None:
            raise DataAccessEnvelopeConflict(
                "Nested data access lineage must reference an existing source row in its immediate parent envelope.",
                context={
                    "source_parent_id": str(source.source_parent_id),
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "source_version": source.source_version,
                },
            )
        if envelope_id is not None and parent.envelope_id == envelope_id:
            raise DataAccessEnvelopeConflict(
                "A data access source cannot reference a source in its own envelope."
            )
        require_matching_source(parent, source, compare_parent=False)


def source_models(
    db: Session,
    envelope_id: uuid.UUID,
    *,
    for_update: bool,
) -> list[DataAccessEnvelopeSource]:
    statement = (
        select(DataAccessEnvelopeSource)
        .where(DataAccessEnvelopeSource.envelope_id == envelope_id)
        .order_by(
            DataAccessEnvelopeSource.source_type,
            DataAccessEnvelopeSource.source_id,
            DataAccessEnvelopeSource.source_version,
            DataAccessEnvelopeSource.source_parent_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    return list(db.scalars(statement.execution_options(populate_existing=True)).all())


def source_model(envelope_id: uuid.UUID, source) -> DataAccessEnvelopeSource:
    values = {
        "envelope_id": envelope_id,
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_version": source.source_version,
        "source_feed_id": source.source_feed_id,
        "source_parent_id": source.source_parent_id,
        "handling_label_id": source.handling_label_id,
        "captured_policy_revision": source.captured_policy_revision,
        "source_digest": source.source_digest,
    }
    if source.captured_at is not None:
        values["captured_at"] = source.captured_at
    return DataAccessEnvelopeSource(**values)


def source_identity(source) -> tuple[str, str, str, uuid.UUID | None]:
    return (
        source.source_type,
        source.source_id,
        source.source_version,
        source.source_parent_id,
    )


def source_identity_from_model(
    source: DataAccessEnvelopeSource,
) -> tuple[str, str, str, uuid.UUID | None]:
    return (
        source.source_type,
        source.source_id,
        source.source_version,
        source.source_parent_id,
    )


def require_matching_source(
    existing: DataAccessEnvelopeSource,
    source,
    *,
    compare_parent: bool = True,
) -> None:
    from app.services.data_access_envelopes import DataAccessEnvelopeConflict

    differs = (
        existing.source_feed_id != source.source_feed_id
        or existing.handling_label_id != source.handling_label_id
        or existing.captured_policy_revision != source.captured_policy_revision
        or existing.source_digest != source.source_digest
        or (compare_parent and existing.source_parent_id != source.source_parent_id)
        or (
            source.captured_at is not None
            and existing.captured_at != source.captured_at
        )
    )
    if differs:
        raise DataAccessEnvelopeConflict(
            "A data access source identity cannot be rewritten with different provenance.",
            context={
                "source_type": source.source_type,
                "source_id": source.source_id,
                "source_version": source.source_version,
            },
        )


def assert_sources_not_referenced(db: Session, source_ids: Sequence[uuid.UUID]) -> None:
    from app.services.data_access_envelopes import DataAccessEnvelopeConflict

    if not source_ids:
        return
    referenced = db.execute(
        select(
            DataAccessEnvelopeSource.source_parent_id,
            DataAccessEnvelopeSource.envelope_id,
        )
        .where(DataAccessEnvelopeSource.source_parent_id.in_(source_ids))
        .limit(1)
    ).first()
    if referenced is not None:
        raise DataAccessEnvelopeConflict(
            "Data access lineage retained by a descendant cannot be removed.",
            context={
                "source_parent_id": str(referenced.source_parent_id),
                "descendant_envelope_id": str(referenced.envelope_id),
            },
        )


def taint_sources_for_feed(
    db: Session,
    *,
    feed_id: uuid.UUID,
    handling_label_id: uuid.UUID,
    policy_revision: int,
) -> int:
    parameters = {
        "feed_id": feed_id,
        "source_id": str(feed_id),
        "source_version": f"policy:{policy_revision}",
        "handling_label_id": handling_label_id,
        "policy_revision": policy_revision,
    }
    conflicting = db.scalar(
        text(
            """
            SELECT count(*)
            FROM data_access_envelope_sources
            WHERE source_type = 'feed_taint'
              AND source_id = :source_id
              AND source_version = :source_version
              AND (
                    source_feed_id IS DISTINCT FROM :feed_id
                 OR source_parent_id IS NOT NULL
                 OR handling_label_id IS DISTINCT FROM :handling_label_id
                 OR captured_policy_revision IS DISTINCT FROM :policy_revision
                 OR source_digest IS NOT NULL
              )
            """
        ),
        parameters,
    )
    if conflicting:
        raise DataPolicyUnavailable(
            "Existing feed-taint lineage conflicts with the current policy revision. Repair provenance before continuing.",
            context={"feed_id": str(feed_id)},
        )

    inserted = int(
        db.scalar(
            text(
                """
                WITH affected AS MATERIALIZED (
                    SELECT DISTINCT envelope_id
                    FROM data_access_envelope_sources
                    WHERE source_feed_id = :feed_id
                ),
                inserted AS (
                    INSERT INTO data_access_envelope_sources (
                        id, envelope_id, source_type, source_id, source_version,
                        source_feed_id, source_parent_id, handling_label_id,
                        captured_policy_revision, source_digest, captured_at
                    )
                    SELECT md5(
                               affected.envelope_id::text || ':feed_taint:' ||
                               :source_id || ':' || :source_version
                           )::uuid,
                           affected.envelope_id, 'feed_taint', :source_id,
                           :source_version, :feed_id, NULL::uuid,
                           :handling_label_id, :policy_revision, NULL::text, now()
                    FROM affected
                    ON CONFLICT ON CONSTRAINT
                        uq_data_access_envelope_sources_identity
                    DO NOTHING
                    RETURNING envelope_id
                )
                SELECT count(*) FROM inserted
                """
            ),
            parameters,
        )
        or 0
    )
    if inserted == 0:
        return 0

    affected_cte = """
        SELECT envelope_id
        FROM data_access_envelope_sources
        WHERE source_type = 'feed_taint'
          AND source_id = :source_id
          AND source_version = :source_version
    """
    db.execute(
        text(
            f"""
            WITH affected AS ({affected_cte})
            DELETE FROM data_access_envelope_labels AS label
            USING affected
            WHERE label.envelope_id = affected.envelope_id
            """
        ),
        parameters,
    )
    db.execute(
        text(
            f"""
            WITH affected AS ({affected_cte})
            INSERT INTO data_access_envelope_labels
                (envelope_id, label_id, source_count)
            SELECT source.envelope_id, source.handling_label_id,
                   count(*)::integer
            FROM data_access_envelope_sources AS source
            JOIN affected ON affected.envelope_id = source.envelope_id
            GROUP BY source.envelope_id, source.handling_label_id
            """
        ),
        parameters,
    )
    db.execute(
        text(
            f"""
            WITH affected AS ({affected_cte}),
            totals AS (
                SELECT source.envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources AS source
                JOIN affected ON affected.envelope_id = source.envelope_id
                GROUP BY source.envelope_id
            )
            UPDATE data_access_envelopes AS envelope
            SET source_count = totals.source_count,
                policy_revision = :policy_revision,
                updated_at = now()
            FROM totals
            WHERE envelope.id = totals.envelope_id
            """
        ),
        parameters,
    )
    inconsistent = db.scalar(
        text(
            f"""
            WITH affected AS ({affected_cte}),
            source_totals AS (
                SELECT source.envelope_id, count(*)::integer AS source_count
                FROM data_access_envelope_sources AS source
                JOIN affected ON affected.envelope_id = source.envelope_id
                GROUP BY source.envelope_id
            ),
            label_totals AS (
                SELECT label.envelope_id,
                       sum(label.source_count)::integer AS source_count
                FROM data_access_envelope_labels AS label
                JOIN affected ON affected.envelope_id = label.envelope_id
                GROUP BY label.envelope_id
            )
            SELECT count(*)
            FROM affected
            JOIN data_access_envelopes AS envelope
              ON envelope.id = affected.envelope_id
            LEFT JOIN source_totals AS source
              ON source.envelope_id = affected.envelope_id
            LEFT JOIN label_totals AS label
              ON label.envelope_id = affected.envelope_id
            WHERE envelope.source_count IS DISTINCT FROM source.source_count
               OR source.source_count IS DISTINCT FROM label.source_count
            """
        ),
        parameters,
    )
    if inconsistent:
        raise DataPolicyUnavailable(
            "Feed-taint lineage and aggregate counts are inconsistent. Repair provenance before continuing.",
            context={"feed_id": str(feed_id)},
        )
    db.expire_all()
    return inserted


def source_label_counts(sources: Sequence) -> dict[uuid.UUID, int]:
    counts: dict[uuid.UUID, int] = {}
    for source in sources:
        counts[source.handling_label_id] = counts.get(source.handling_label_id, 0) + 1
    return counts


def rebuild_source_aggregates(
    db: Session,
    envelope: DataAccessEnvelope,
    *,
    current_revision: int,
) -> None:
    from app.services.data_access_envelopes import (
        DataAccessEnvelopeConflict,
        _label_counts,
        _validate_active_labels,
    )

    source_rows = source_models(db, envelope.id, for_update=True)
    if not source_rows:
        raise DataAccessEnvelopeConflict(
            "A normalized data access envelope cannot have an empty source set."
        )
    counts = source_label_counts(source_rows)
    _validate_active_labels(db, counts)
    db.execute(
        delete(DataAccessEnvelopeLabel).where(
            DataAccessEnvelopeLabel.envelope_id == envelope.id
        )
    )
    db.add_all(
        [
            DataAccessEnvelopeLabel(
                envelope_id=envelope.id,
                label_id=label_id,
                source_count=count,
            )
            for label_id, count in counts.items()
        ]
    )
    envelope.source_count = len(source_rows)
    envelope.policy_revision = current_revision
    db.add(envelope)
    db.flush()
    validate_normalized_source_invariants(
        db,
        envelope=envelope,
        aggregate_counts=_label_counts(db, envelope.id),
        source_rows=source_rows,
    )


def validate_normalized_source_invariants(
    db: Session,
    *,
    envelope: DataAccessEnvelope,
    aggregate_counts: Mapping[uuid.UUID, int],
    source_rows: Sequence[DataAccessEnvelopeSource] | None = None,
) -> None:
    from app.services.data_access_envelopes import _validate_active_labels

    rows = (
        list(source_rows)
        if source_rows is not None
        else source_models(db, envelope.id, for_update=False)
    )
    if not rows:
        raise DataPolicyUnavailable(
            "The normalized data access envelope has no source lineage."
        )
    source_counts = source_label_counts(rows)
    max_captured_revision = max(row.captured_policy_revision for row in rows)
    if (
        envelope.source_count != len(rows)
        or dict(aggregate_counts) != source_counts
        or sum(aggregate_counts.values()) != len(rows)
        or envelope.policy_revision < max_captured_revision
    ):
        raise DataPolicyUnavailable(
            "Data access envelope source and aggregate counts are inconsistent. Repair provenance before continuing.",
            context={"envelope_id": str(envelope.id)},
        )
    _validate_active_labels(db, source_counts)


def source_snapshot(source: DataAccessEnvelopeSource):
    from app.services.data_access_envelopes import DataAccessSourceSnapshot

    return DataAccessSourceSnapshot(
        id=source.id,
        envelope_id=source.envelope_id,
        source_type=source.source_type,
        source_id=source.source_id,
        source_version=source.source_version,
        source_feed_id=source.source_feed_id,
        source_parent_id=source.source_parent_id,
        handling_label_id=source.handling_label_id,
        captured_policy_revision=source.captured_policy_revision,
        source_digest=source.source_digest,
        captured_at=source.captured_at,
    )


def has_normalized_sources(db: Session, envelope_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(DataAccessEnvelopeSource.id)
            .where(DataAccessEnvelopeSource.envelope_id == envelope_id)
            .limit(1)
        )
    )


def _normalize_source_text(value: str, *, field_name: str, max_length: int) -> str:
    from app.services.data_access_envelopes import DataAccessEnvelopeConflict

    if not isinstance(value, str):
        raise DataAccessEnvelopeConflict(
            f"Data access source {field_name} values must be strings."
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise DataAccessEnvelopeConflict(
            f"Data access source {field_name} is empty, too long, or contains control characters."
        )
    return normalized


def _validate_captured_policy_revision(value: int, current_revision: int) -> None:
    from app.services.data_access_envelopes import DataAccessEnvelopeConflict

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DataAccessEnvelopeConflict(
            "Data access sources require a positive captured policy revision."
        )
    if value > current_revision:
        raise DataAccessEnvelopeConflict(
            "A data access source cannot reference a future policy revision.",
            context={
                "captured_policy_revision": value,
                "current_policy_revision": current_revision,
            },
        )


__all__ = [
    "assert_sources_not_referenced",
    "has_normalized_sources",
    "normalize_sources",
    "rebuild_source_aggregates",
    "require_matching_source",
    "source_identity",
    "source_identity_from_model",
    "source_label_counts",
    "source_model",
    "source_models",
    "source_snapshot",
    "taint_sources_for_feed",
    "validate_normalized_source_invariants",
    "validate_source_references",
]
