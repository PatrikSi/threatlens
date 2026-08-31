"""separate captured metric labels from monotonic relabel taint

Revision ID: 0079_metric_captured_taint
Revises: 0078_ai_telemetry_policy
Create Date: 2026-08-31
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from itertools import combinations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0079_metric_captured_taint"
down_revision = "0078_ai_telemetry_policy"
branch_labels = None
depends_on = None


_QUARANTINE_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")
_UNRESOLVED_FEED_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_ALERT_FEED_TAINT_FUNCTION = "threatlens_taint_alert_metrics_for_feed_v1"
_INTEGRATION_FEED_TAINT_FUNCTION = "threatlens_taint_integration_metrics_for_feed_v1"
_ALERT_EFFECTIVE_FUNCTION = "threatlens_classify_alert_metric_label_v1"
_ALERT_EFFECTIVE_TRIGGER = "trg_alert_metric_labels_classify_v1"
_ALERT_ORIGIN_FUNCTION = "threatlens_sync_alert_metric_label_v1"
_ALERT_CAPTURED_TRIGGER = "trg_alert_metric_captured_sync_v1"
_ALERT_TAINT_TRIGGER = "trg_alert_metric_taint_sync_v1"
_INTEGRATION_EFFECTIVE_FUNCTION = "threatlens_classify_integration_metric_label_v1"
_INTEGRATION_EFFECTIVE_TRIGGER = "trg_integration_metric_labels_classify_v1"
_INTEGRATION_ORIGIN_FUNCTION = "threatlens_sync_integration_metric_label_v1"
_INTEGRATION_CAPTURED_TRIGGER = "trg_integration_metric_captured_sync_v1"
_INTEGRATION_TAINT_TRIGGER = "trg_integration_metric_taint_sync_v1"
_ALERT_MUTATION_FUNCTION = "threatlens_guard_alert_metric_labels_v1"
_INTEGRATION_MUTATION_FUNCTION = "threatlens_guard_integration_metric_labels_v1"
_ALERT_COMPAT_FUNCTION = "threatlens_capture_alert_metric_metadata_v1"
_ALERT_COMPAT_TRIGGER = "trg_alert_metric_metadata_compat_v1"
_CAPTURE_SEARCH_BUDGET = 4_096
_ALERT_REVISION_PROBE_LIMIT = 256


def upgrade() -> None:
    bind = op.get_bind()
    policy_revision = _require_disabled_data_policy(bind, operation="migrate")
    _lock_metric_lineage(bind, include_origins=False)
    quarantine_active = bind.scalar(
        sa.text("SELECT is_active FROM handling_labels WHERE id = :label_id"),
        {"label_id": _QUARANTINE_LABEL_ID},
    )
    if quarantine_active is not True:
        raise RuntimeError(
            "Cannot migrate metric captured provenance because the quarantine "
            "handling label is missing or inactive."
        )

    op.add_column(
        "alert_occurrence_metric_cohorts",
        sa.Column("captured_policy_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "alert_occurrence_metric_cohorts",
        sa.Column("provenance_complete", sa.Boolean(), nullable=True),
    )
    _create_origin_table(
        "alert_occurrence_metric_cohort_captured_labels",
        "alert_occurrence_metric_cohorts",
        "ix_alert_metric_captured_labels_label",
    )
    _create_origin_table(
        "alert_occurrence_metric_cohort_taint_labels",
        "alert_occurrence_metric_cohorts",
        "ix_alert_metric_taint_labels_label",
    )
    _create_origin_table(
        "integration_delivery_metric_cohort_captured_labels",
        "integration_delivery_metric_cohorts",
        "ix_integration_metric_captured_labels_label",
    )
    _create_origin_table(
        "integration_delivery_metric_cohort_taint_labels",
        "integration_delivery_metric_cohorts",
        "ix_integration_metric_taint_labels_label",
    )

    op.drop_constraint(
        "uq_alert_occurrence_metric_cohorts_dimensions",
        "alert_occurrence_metric_cohorts",
        type_="unique",
    )
    op.drop_constraint(
        "uq_integration_delivery_metric_cohorts_dimensions",
        "integration_delivery_metric_cohorts",
        type_="unique",
    )
    _repair_alert_cohorts(bind, policy_revision=policy_revision)
    _repair_integration_cohorts(bind, policy_revision=policy_revision)
    op.create_unique_constraint(
        "uq_alert_occurrence_metric_cohorts_dimensions",
        "alert_occurrence_metric_cohorts",
        ["metric_id", "source_feed_id_snapshot", "policy_cohort_key"],
    )
    op.create_unique_constraint(
        "uq_integration_delivery_metric_cohorts_dimensions",
        "integration_delivery_metric_cohorts",
        ["metric_id", "policy_cohort_key"],
    )
    op.alter_column(
        "alert_occurrence_metric_cohorts",
        "captured_policy_revision",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "alert_occurrence_metric_cohorts",
        "provenance_complete",
        existing_type=sa.Boolean(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_alert_occurrence_metric_cohorts_key",
        "alert_occurrence_metric_cohorts",
        "policy_cohort_key ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_alert_occurrence_metric_cohorts_revision",
        "alert_occurrence_metric_cohorts",
        "captured_policy_revision >= 0",
    )
    _validate_metric_integrity(bind)
    _install_alert_cohort_compatibility(bind)
    _install_sync_triggers(bind)
    _install_mutation_fences(bind)
    _install_feed_taint_functions(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _require_disabled_data_policy(bind, operation="downgrade")
    _lock_metric_lineage(bind, include_origins=True)
    _validate_metric_integrity(bind)
    _restore_legacy_integration_keys(bind)
    _drop_mutation_fences(bind)
    _drop_sync_triggers(bind)
    _drop_alert_cohort_compatibility(bind)
    _restore_legacy_feed_taint_functions(bind)

    op.drop_constraint(
        "ck_alert_occurrence_metric_cohorts_revision",
        "alert_occurrence_metric_cohorts",
        type_="check",
    )
    op.drop_constraint(
        "ck_alert_occurrence_metric_cohorts_key",
        "alert_occurrence_metric_cohorts",
        type_="check",
    )
    op.drop_column("alert_occurrence_metric_cohorts", "provenance_complete")
    op.drop_column("alert_occurrence_metric_cohorts", "captured_policy_revision")
    _drop_origin_table(
        "integration_delivery_metric_cohort_taint_labels",
        "ix_integration_metric_taint_labels_label",
    )
    _drop_origin_table(
        "integration_delivery_metric_cohort_captured_labels",
        "ix_integration_metric_captured_labels_label",
    )
    _drop_origin_table(
        "alert_occurrence_metric_cohort_taint_labels",
        "ix_alert_metric_taint_labels_label",
    )
    _drop_origin_table(
        "alert_occurrence_metric_cohort_captured_labels",
        "ix_alert_metric_captured_labels_label",
    )


def _create_origin_table(table_name: str, cohort_table: str, index_name: str) -> None:
    op.create_table(
        table_name,
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            [f"{cohort_table}.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["handling_labels.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("cohort_id", "label_id"),
    )
    op.create_index(index_name, table_name, ["label_id", "cohort_id"])


def _drop_origin_table(table_name: str, index_name: str) -> None:
    op.drop_index(index_name, table_name=table_name)
    op.drop_table(table_name)


def _require_disabled_data_policy(bind, *, operation: str) -> int:
    state = (
        bind.execute(
            sa.text(
                "SELECT mode, coverage_version, revision FROM data_policy_state "
                "WHERE id = 1 FOR UPDATE"
            )
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        raise RuntimeError(
            f"Cannot {operation} metric captured provenance because data policy "
            "state is missing."
        )
    if state["mode"] != "disabled" or int(state["coverage_version"] or 0) != 0:
        raise RuntimeError(
            f"Cannot {operation} metric captured provenance while data policy "
            "audit or enforcement is active. Disable data policy first."
        )
    return int(state["revision"])


def _lock_metric_lineage(bind, *, include_origins: bool) -> None:
    tables = [
        "feeds",
        "handling_labels",
        "alert_occurrences",
        "alert_occurrence_metrics",
        "alert_occurrence_metric_cohorts",
        "alert_occurrence_metric_cohort_labels",
        "integration_deliveries",
        "integration_delivery_metrics",
        "integration_delivery_metric_cohorts",
        "integration_delivery_metric_cohort_labels",
        "integration_delivery_metric_cohort_feeds",
        "data_access_envelopes",
        "data_access_envelope_sources",
        "data_access_envelope_labels",
    ]
    if include_origins:
        tables.extend(
            [
                "alert_occurrence_metric_cohort_captured_labels",
                "alert_occurrence_metric_cohort_taint_labels",
                "integration_delivery_metric_cohort_captured_labels",
                "integration_delivery_metric_cohort_taint_labels",
            ]
        )
    bind.execute(sa.text(f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE"))


def _metric_key(policy_revision: int, label_ids) -> str:
    labels = "|".join(sorted({str(value) for value in label_ids}))
    canonical = f"{max(0, int(policy_revision))}:{labels}"
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _legacy_integration_key(
    *,
    policy_revision: int,
    provenance_complete: bool,
    source_count: int,
    label_ids,
    feed_ids,
) -> str:
    labels = "|".join(sorted({str(value) for value in label_ids}))
    feeds = "|".join(sorted({str(value) for value in feed_ids}))
    canonical = (
        f"{max(1, int(policy_revision))}:"
        f"{int(bool(provenance_complete))}:{max(0, int(source_count))}:"
        f"{labels}:{feeds}"
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _repair_alert_cohorts(bind, *, policy_revision: int) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, metric_id, source_feed_id_snapshot,
                       policy_cohort_key, occurrence_count, created_at, updated_at
                FROM alert_occurrence_metric_cohorts
                ORDER BY id
                """
            )
        ).mappings()
    )
    effective = _label_sets(
        bind,
        table="alert_occurrence_metric_cohort_labels",
    )
    grouped: dict[tuple[uuid.UUID, uuid.UUID, str], list[dict]] = defaultdict(list)
    capture_cache: dict[
        tuple[str, tuple[uuid.UUID, ...], int],
        tuple[int, frozenset[uuid.UUID]] | None,
    ] = {}
    for row in rows:
        labels = effective[row["id"]]
        matched = _matching_alert_capture(
            policy_cohort_key=row["policy_cohort_key"],
            label_ids=labels,
            maximum_revision=policy_revision,
            cache=capture_cache,
        )
        if matched is None:
            captured_revision = max(0, policy_revision)
            captured_labels = {_QUARANTINE_LABEL_ID}
            taint_labels = set(labels)
            provenance_complete = False
        else:
            captured_revision, captured_labels = matched
            taint_labels = set(labels) - captured_labels
            provenance_complete = row["source_feed_id_snapshot"] != _UNRESOLVED_FEED_ID
        cohort_key = _metric_key(captured_revision, captured_labels)
        grouped[(row["metric_id"], row["source_feed_id_snapshot"], cohort_key)].append(
            {
                **row,
                "captured_policy_revision": captured_revision,
                "captured_labels": captured_labels,
                "taint_labels": taint_labels,
                "provenance_complete": provenance_complete,
                "new_key": cohort_key,
            }
        )

    bind.execute(sa.text("DELETE FROM alert_occurrence_metric_cohort_labels"))
    bind.execute(sa.text("DELETE FROM alert_occurrence_metric_cohorts"))
    for records in grouped.values():
        keeper = min(records, key=lambda value: str(value["id"]))
        captured_labels = set(keeper["captured_labels"])
        captured_revision = keeper["captured_policy_revision"]
        if any(
            record["captured_labels"] != captured_labels
            or record["captured_policy_revision"] != captured_revision
            for record in records
        ):
            raise RuntimeError(
                "Alert metric captured cohort digest collision detected."
            )
        taint_labels = set().union(*(record["taint_labels"] for record in records))
        bind.execute(
            sa.text(
                """
                INSERT INTO alert_occurrence_metric_cohorts (
                    id, metric_id, source_feed_id_snapshot, policy_cohort_key,
                    captured_policy_revision, provenance_complete,
                    occurrence_count, created_at, updated_at
                ) VALUES (
                    :id, :metric_id, :source_feed_id, :cohort_key,
                    :captured_revision, :provenance_complete,
                    :occurrence_count, :created_at, :updated_at
                )
                """
            ),
            {
                "id": keeper["id"],
                "metric_id": keeper["metric_id"],
                "source_feed_id": keeper["source_feed_id_snapshot"],
                "cohort_key": keeper["new_key"],
                "captured_revision": captured_revision,
                "provenance_complete": all(
                    record["provenance_complete"] for record in records
                ),
                "occurrence_count": sum(
                    int(record["occurrence_count"]) for record in records
                ),
                "created_at": min(record["created_at"] for record in records),
                "updated_at": max(record["updated_at"] for record in records),
            },
        )
        _insert_label_origins(
            bind,
            cohort_id=keeper["id"],
            captured_labels=captured_labels,
            taint_labels=taint_labels,
            captured_table="alert_occurrence_metric_cohort_captured_labels",
            taint_table="alert_occurrence_metric_cohort_taint_labels",
            effective_table="alert_occurrence_metric_cohort_labels",
        )


def _repair_integration_cohorts(bind, *, policy_revision: int) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, metric_id, policy_cohort_key,
                       captured_policy_revision, provenance_complete,
                       source_count, succeeded_count, failed_count,
                       dead_letter_count, attempt_count, duration_total_ms,
                       duration_max_ms, created_at, updated_at
                FROM integration_delivery_metric_cohorts
                ORDER BY id
                """
            )
        ).mappings()
    )
    effective = _label_sets(
        bind,
        table="integration_delivery_metric_cohort_labels",
    )
    feeds = _feed_sets(bind)
    grouped: dict[tuple[uuid.UUID, str], list[dict]] = defaultdict(list)
    for row in rows:
        labels = effective[row["id"]]
        feed_ids = feeds[row["id"]]
        captured_labels = _matching_integration_capture(
            row=row,
            label_ids=labels,
            feed_ids=feed_ids,
        )
        captured_revision = int(row["captured_policy_revision"])
        provenance_complete = bool(row["provenance_complete"])
        source_count = int(row["source_count"])
        if (
            captured_labels is None
            or captured_revision > policy_revision
            or (not provenance_complete and _QUARANTINE_LABEL_ID not in captured_labels)
        ):
            captured_revision = max(1, policy_revision)
            captured_labels = {_QUARANTINE_LABEL_ID}
            taint_labels = set(labels)
            provenance_complete = False
        else:
            taint_labels = set(labels) - captured_labels
        cohort_key = _legacy_integration_key(
            policy_revision=captured_revision,
            provenance_complete=provenance_complete,
            source_count=source_count,
            label_ids=captured_labels,
            feed_ids=feed_ids,
        )
        grouped[(row["metric_id"], cohort_key)].append(
            {
                **row,
                "captured_policy_revision": captured_revision,
                "captured_labels": captured_labels,
                "taint_labels": taint_labels,
                "provenance_complete": provenance_complete,
                "source_count": source_count,
                "feed_ids": feed_ids,
                "new_key": cohort_key,
            }
        )

    bind.execute(sa.text("DELETE FROM integration_delivery_metric_cohort_labels"))
    bind.execute(sa.text("DELETE FROM integration_delivery_metric_cohort_feeds"))
    bind.execute(sa.text("DELETE FROM integration_delivery_metric_cohorts"))
    for records in grouped.values():
        keeper = min(records, key=lambda value: str(value["id"]))
        immutable = (
            keeper["captured_policy_revision"],
            keeper["provenance_complete"],
            keeper["source_count"],
            keeper["captured_labels"],
            keeper["feed_ids"],
        )
        if any(
            (
                record["captured_policy_revision"],
                record["provenance_complete"],
                record["source_count"],
                record["captured_labels"],
                record["feed_ids"],
            )
            != immutable
            for record in records
        ):
            raise RuntimeError(
                "Integration metric captured cohort digest collision detected."
            )
        taint_labels = set().union(*(record["taint_labels"] for record in records))
        bind.execute(
            sa.text(
                """
                INSERT INTO integration_delivery_metric_cohorts (
                    id, metric_id, policy_cohort_key,
                    captured_policy_revision, provenance_complete, source_count,
                    succeeded_count, failed_count, dead_letter_count,
                    attempt_count, duration_total_ms, duration_max_ms,
                    created_at, updated_at
                ) VALUES (
                    :id, :metric_id, :cohort_key, :captured_revision,
                    :provenance_complete, :source_count, :succeeded_count,
                    :failed_count, :dead_letter_count, :attempt_count,
                    :duration_total_ms, :duration_max_ms, :created_at, :updated_at
                )
                """
            ),
            {
                "id": keeper["id"],
                "metric_id": keeper["metric_id"],
                "cohort_key": keeper["new_key"],
                "captured_revision": keeper["captured_policy_revision"],
                "provenance_complete": keeper["provenance_complete"],
                "source_count": keeper["source_count"],
                "succeeded_count": sum(
                    int(record["succeeded_count"]) for record in records
                ),
                "failed_count": sum(int(record["failed_count"]) for record in records),
                "dead_letter_count": sum(
                    int(record["dead_letter_count"]) for record in records
                ),
                "attempt_count": sum(
                    int(record["attempt_count"]) for record in records
                ),
                "duration_total_ms": sum(
                    int(record["duration_total_ms"]) for record in records
                ),
                "duration_max_ms": max(
                    int(record["duration_max_ms"]) for record in records
                ),
                "created_at": min(record["created_at"] for record in records),
                "updated_at": max(record["updated_at"] for record in records),
            },
        )
        _insert_label_origins(
            bind,
            cohort_id=keeper["id"],
            captured_labels=keeper["captured_labels"],
            taint_labels=taint_labels,
            captured_table="integration_delivery_metric_cohort_captured_labels",
            taint_table="integration_delivery_metric_cohort_taint_labels",
            effective_table="integration_delivery_metric_cohort_labels",
        )
        for feed_id in sorted(keeper["feed_ids"], key=str):
            bind.execute(
                sa.text(
                    "INSERT INTO integration_delivery_metric_cohort_feeds "
                    "(cohort_id, source_feed_id_snapshot) VALUES "
                    "(:cohort_id, :feed_id)"
                ),
                {"cohort_id": keeper["id"], "feed_id": feed_id},
            )


def _matching_alert_capture(
    *,
    policy_cohort_key: str,
    label_ids: set[uuid.UUID],
    maximum_revision: int,
    cache: dict[
        tuple[str, tuple[uuid.UUID, ...], int],
        tuple[int, frozenset[uuid.UUID]] | None,
    ],
) -> tuple[int, set[uuid.UUID]] | None:
    ordered_labels = tuple(sorted(label_ids, key=str))
    cache_key = (policy_cohort_key, ordered_labels, maximum_revision)
    if cache_key in cache:
        cached = cache[cache_key]
        return None if cached is None else (cached[0], set(cached[1]))
    if not ordered_labels:
        cache[cache_key] = None
        return None
    revisions = list(
        range(min(max(0, maximum_revision), _ALERT_REVISION_PROBE_LIMIT - 1) + 1)
    )
    if maximum_revision >= 0 and maximum_revision not in revisions:
        revisions.append(maximum_revision)
    matches: list[tuple[int, set[uuid.UUID]]] = []
    attempts = 0
    candidates = [set(ordered_labels)]
    candidates.extend(
        candidate
        for candidate in _nonempty_subsets(set(ordered_labels))
        if len(candidate) != len(ordered_labels)
    )
    for candidate in candidates:
        for candidate_revision in revisions:
            if attempts >= _CAPTURE_SEARCH_BUDGET:
                cache[cache_key] = None
                return None
            attempts += 1
            if _metric_key(candidate_revision, candidate) == policy_cohort_key:
                matches.append((candidate_revision, candidate))
                if len(matches) > 1:
                    cache[cache_key] = None
                    return None
    result = matches[0] if len(matches) == 1 else None
    cache[cache_key] = None if result is None else (result[0], frozenset(result[1]))
    return result


def _matching_integration_capture(
    *,
    row,
    label_ids: set[uuid.UUID],
    feed_ids: set[uuid.UUID],
) -> set[uuid.UUID] | None:
    if not label_ids or len(label_ids) > 12:
        return None
    matches = [
        candidate
        for candidate in _nonempty_subsets(label_ids)
        if _legacy_integration_key(
            policy_revision=row["captured_policy_revision"],
            provenance_complete=row["provenance_complete"],
            source_count=row["source_count"],
            label_ids=candidate,
            feed_ids=feed_ids,
        )
        == row["policy_cohort_key"]
    ]
    return matches[0] if len(matches) == 1 else None


def _nonempty_subsets(values: set[uuid.UUID]):
    ordered = sorted(values, key=str)
    for length in range(1, len(ordered) + 1):
        for candidate in combinations(ordered, length):
            yield set(candidate)


def _label_sets(bind, *, table: str) -> dict[uuid.UUID, set[uuid.UUID]]:
    labels: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for cohort_id, label_id in bind.execute(
        sa.text(f"SELECT cohort_id, label_id FROM {table}")
    ):
        labels[cohort_id].add(label_id)
    return labels


def _feed_sets(bind) -> dict[uuid.UUID, set[uuid.UUID]]:
    feeds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for cohort_id, feed_id in bind.execute(
        sa.text(
            "SELECT cohort_id, source_feed_id_snapshot "
            "FROM integration_delivery_metric_cohort_feeds"
        )
    ):
        feeds[cohort_id].add(feed_id)
    return feeds


def _insert_label_origins(
    bind,
    *,
    cohort_id: uuid.UUID,
    captured_labels: set[uuid.UUID],
    taint_labels: set[uuid.UUID],
    captured_table: str,
    taint_table: str,
    effective_table: str,
) -> None:
    for table, labels in (
        (captured_table, captured_labels),
        (taint_table, taint_labels),
        (effective_table, captured_labels | taint_labels),
    ):
        for label_id in sorted(labels, key=str):
            bind.execute(
                sa.text(
                    f"INSERT INTO {table} (cohort_id, label_id) "
                    "VALUES (:cohort_id, :label_id)"
                ),
                {"cohort_id": cohort_id, "label_id": label_id},
            )


def _validate_metric_integrity(bind) -> None:
    problems = 0
    for cohort_table, captured_table, taint_table, effective_table, integration in (
        (
            "alert_occurrence_metric_cohorts",
            "alert_occurrence_metric_cohort_captured_labels",
            "alert_occurrence_metric_cohort_taint_labels",
            "alert_occurrence_metric_cohort_labels",
            False,
        ),
        (
            "integration_delivery_metric_cohorts",
            "integration_delivery_metric_cohort_captured_labels",
            "integration_delivery_metric_cohort_taint_labels",
            "integration_delivery_metric_cohort_labels",
            True,
        ),
    ):
        captured = _label_sets(bind, table=captured_table)
        taints = _label_sets(bind, table=taint_table)
        effective = _label_sets(bind, table=effective_table)
        feeds = _feed_sets(bind) if integration else defaultdict(set)
        columns = "id, policy_cohort_key, captured_policy_revision, provenance_complete"
        if integration:
            columns += ", source_count"
        rows = bind.execute(sa.text(f"SELECT {columns} FROM {cohort_table}")).mappings()
        for row in rows:
            captured_labels = captured[row["id"]]
            expected = (
                _legacy_integration_key(
                    policy_revision=row["captured_policy_revision"],
                    provenance_complete=row["provenance_complete"],
                    source_count=row["source_count"],
                    label_ids=captured_labels,
                    feed_ids=feeds[row["id"]],
                )
                if integration
                else _metric_key(row["captured_policy_revision"], captured_labels)
            )
            problems += int(not captured_labels)
            problems += int(expected != row["policy_cohort_key"])
            problems += int(effective[row["id"]] != captured_labels | taints[row["id"]])
            problems += int(
                not row["provenance_complete"]
                and _QUARANTINE_LABEL_ID not in captured_labels
            )

    alert_mismatches = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM alert_occurrence_metrics AS metric
                LEFT JOIN (
                    SELECT metric_id, sum(occurrence_count) AS occurrence_count
                    FROM alert_occurrence_metric_cohorts GROUP BY metric_id
                ) AS totals ON totals.metric_id = metric.id
                WHERE metric.occurrence_count
                    <> coalesce(totals.occurrence_count, 0)
                """
            )
        )
        or 0
    )
    integration_mismatches = int(
        bind.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM integration_delivery_metrics AS metric
                LEFT JOIN (
                    SELECT metric_id,
                           sum(succeeded_count) AS succeeded_count,
                           sum(failed_count) AS failed_count,
                           sum(dead_letter_count) AS dead_letter_count,
                           sum(attempt_count) AS attempt_count,
                           sum(duration_total_ms) AS duration_total_ms,
                           max(duration_max_ms) AS duration_max_ms
                    FROM integration_delivery_metric_cohorts GROUP BY metric_id
                ) AS totals ON totals.metric_id = metric.id
                WHERE metric.succeeded_count
                          <> coalesce(totals.succeeded_count, 0)
                   OR metric.failed_count <> coalesce(totals.failed_count, 0)
                   OR metric.dead_letter_count
                          <> coalesce(totals.dead_letter_count, 0)
                   OR metric.attempt_count <> coalesce(totals.attempt_count, 0)
                   OR metric.duration_total_ms
                          <> coalesce(totals.duration_total_ms, 0)
                   OR metric.duration_max_ms
                          <> coalesce(totals.duration_max_ms, 0)
                """
            )
        )
        or 0
    )
    if problems or alert_mismatches or integration_mismatches:
        raise RuntimeError(
            "Metric captured provenance failed identity, label, or aggregate "
            "parity validation. Repair the affected cohorts before continuing."
        )


def _restore_legacy_integration_keys(bind) -> None:
    captured = _label_sets(
        bind,
        table="integration_delivery_metric_cohort_captured_labels",
    )
    feeds = _feed_sets(bind)
    rows = bind.execute(
        sa.text(
            """
            SELECT id, captured_policy_revision, provenance_complete, source_count
            FROM integration_delivery_metric_cohorts
            """
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE integration_delivery_metric_cohorts "
                "SET policy_cohort_key = :cohort_key WHERE id = :cohort_id"
            ),
            {
                "cohort_id": row["id"],
                "cohort_key": _legacy_integration_key(
                    policy_revision=row["captured_policy_revision"],
                    provenance_complete=row["provenance_complete"],
                    source_count=row["source_count"],
                    label_ids=captured[row["id"]],
                    feed_ids=feeds[row["id"]],
                ),
            },
        )


def _install_sync_triggers(bind) -> None:
    _install_metric_sync(
        bind,
        effective_table="alert_occurrence_metric_cohort_labels",
        captured_table="alert_occurrence_metric_cohort_captured_labels",
        taint_table="alert_occurrence_metric_cohort_taint_labels",
        writer_setting="threatlens.alert_metric_cohort_write",
        effective_function=_ALERT_EFFECTIVE_FUNCTION,
        effective_trigger=_ALERT_EFFECTIVE_TRIGGER,
        origin_function=_ALERT_ORIGIN_FUNCTION,
        captured_trigger=_ALERT_CAPTURED_TRIGGER,
        taint_trigger=_ALERT_TAINT_TRIGGER,
    )
    _install_metric_sync(
        bind,
        effective_table="integration_delivery_metric_cohort_labels",
        captured_table="integration_delivery_metric_cohort_captured_labels",
        taint_table="integration_delivery_metric_cohort_taint_labels",
        writer_setting="threatlens.integration_metric_cohort_write",
        effective_function=_INTEGRATION_EFFECTIVE_FUNCTION,
        effective_trigger=_INTEGRATION_EFFECTIVE_TRIGGER,
        origin_function=_INTEGRATION_ORIGIN_FUNCTION,
        captured_trigger=_INTEGRATION_CAPTURED_TRIGGER,
        taint_trigger=_INTEGRATION_TAINT_TRIGGER,
    )


def _install_alert_cohort_compatibility(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_ALERT_COMPAT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.captured_policy_revision IS NULL THEN
                    SELECT revision INTO NEW.captured_policy_revision
                    FROM data_policy_state WHERE id = 1;
                END IF;
                IF NEW.provenance_complete IS NULL THEN
                    NEW.provenance_complete := (
                        NEW.source_feed_id_snapshot
                        <> '{_UNRESOLVED_FEED_ID}'::uuid
                    );
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"CREATE TRIGGER {_ALERT_COMPAT_TRIGGER} BEFORE INSERT ON "
            "alert_occurrence_metric_cohorts FOR EACH ROW "
            f"EXECUTE FUNCTION {_ALERT_COMPAT_FUNCTION}()"
        )
    )


def _drop_alert_cohort_compatibility(bind) -> None:
    bind.execute(
        sa.text(
            f"DROP TRIGGER {_ALERT_COMPAT_TRIGGER} ON alert_occurrence_metric_cohorts"
        )
    )
    bind.execute(sa.text(f"DROP FUNCTION {_ALERT_COMPAT_FUNCTION}()"))


def _install_metric_sync(
    bind,
    *,
    effective_table: str,
    captured_table: str,
    taint_table: str,
    writer_setting: str,
    effective_function: str,
    effective_trigger: str,
    origin_function: str,
    captured_trigger: str,
    taint_trigger: str,
) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {origin_function}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO {effective_table} (cohort_id, label_id)
                VALUES (NEW.cohort_id, NEW.label_id)
                ON CONFLICT DO NOTHING;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    for table, trigger in (
        (captured_table, captured_trigger),
        (taint_table, taint_trigger),
    ):
        bind.execute(
            sa.text(
                f"CREATE TRIGGER {trigger} AFTER INSERT ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {origin_function}()"
            )
        )
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {effective_function}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM {captured_table}
                    WHERE cohort_id = NEW.cohort_id AND label_id = NEW.label_id
                ) AND NOT EXISTS (
                    SELECT 1 FROM {taint_table}
                    WHERE cohort_id = NEW.cohort_id AND label_id = NEW.label_id
                ) THEN
                    IF current_setting('{writer_setting}', true) = 'on' THEN
                        INSERT INTO {captured_table} (cohort_id, label_id)
                        VALUES (NEW.cohort_id, NEW.label_id)
                        ON CONFLICT DO NOTHING;
                    ELSE
                        INSERT INTO {taint_table} (cohort_id, label_id)
                        VALUES (NEW.cohort_id, NEW.label_id)
                        ON CONFLICT DO NOTHING;
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"CREATE TRIGGER {effective_trigger} AFTER INSERT ON {effective_table} "
            f"FOR EACH ROW EXECUTE FUNCTION {effective_function}()"
        )
    )


def _drop_sync_triggers(bind) -> None:
    for effective_table, captured_table, taint_table, names in (
        (
            "alert_occurrence_metric_cohort_labels",
            "alert_occurrence_metric_cohort_captured_labels",
            "alert_occurrence_metric_cohort_taint_labels",
            (
                _ALERT_EFFECTIVE_TRIGGER,
                _ALERT_EFFECTIVE_FUNCTION,
                _ALERT_CAPTURED_TRIGGER,
                _ALERT_TAINT_TRIGGER,
                _ALERT_ORIGIN_FUNCTION,
            ),
        ),
        (
            "integration_delivery_metric_cohort_labels",
            "integration_delivery_metric_cohort_captured_labels",
            "integration_delivery_metric_cohort_taint_labels",
            (
                _INTEGRATION_EFFECTIVE_TRIGGER,
                _INTEGRATION_EFFECTIVE_FUNCTION,
                _INTEGRATION_CAPTURED_TRIGGER,
                _INTEGRATION_TAINT_TRIGGER,
                _INTEGRATION_ORIGIN_FUNCTION,
            ),
        ),
    ):
        (
            effective_trigger,
            effective_function,
            captured_trigger,
            taint_trigger,
            origin_function,
        ) = names
        bind.execute(sa.text(f"DROP TRIGGER {effective_trigger} ON {effective_table}"))
        bind.execute(sa.text(f"DROP FUNCTION {effective_function}()"))
        bind.execute(sa.text(f"DROP TRIGGER {captured_trigger} ON {captured_table}"))
        bind.execute(sa.text(f"DROP TRIGGER {taint_trigger} ON {taint_table}"))
        bind.execute(sa.text(f"DROP FUNCTION {origin_function}()"))


def _install_mutation_fences(bind) -> None:
    _install_mutation_fence(
        bind,
        cohort_table="alert_occurrence_metric_cohorts",
        function_name=_ALERT_MUTATION_FUNCTION,
        tables=(
            "alert_occurrence_metric_cohort_labels",
            "alert_occurrence_metric_cohort_captured_labels",
            "alert_occurrence_metric_cohort_taint_labels",
        ),
        trigger_prefix="trg_alert_metric_label_fence",
    )
    _install_mutation_fence(
        bind,
        cohort_table="integration_delivery_metric_cohorts",
        function_name=_INTEGRATION_MUTATION_FUNCTION,
        tables=(
            "integration_delivery_metric_cohort_labels",
            "integration_delivery_metric_cohort_captured_labels",
            "integration_delivery_metric_cohort_taint_labels",
        ),
        trigger_prefix="trg_integration_metric_label_fence",
    )


def _install_mutation_fence(
    bind,
    *,
    cohort_table: str,
    function_name: str,
    tables: tuple[str, str, str],
    trigger_prefix: str,
) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {function_name}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' OR EXISTS (
                    SELECT 1 FROM {cohort_table} WHERE id = OLD.cohort_id
                ) THEN
                    RAISE EXCEPTION
                        'Metric cohort label provenance is immutable while its cohort is retained.'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
            END;
            $$
            """
        )
    )
    for index, table in enumerate(tables):
        bind.execute(
            sa.text(
                f"CREATE TRIGGER {trigger_prefix}_{index}_v1 "
                f"BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW "
                f"EXECUTE FUNCTION {function_name}()"
            )
        )


def _drop_mutation_fences(bind) -> None:
    for function_name, tables, trigger_prefix in (
        (
            _ALERT_MUTATION_FUNCTION,
            (
                "alert_occurrence_metric_cohort_labels",
                "alert_occurrence_metric_cohort_captured_labels",
                "alert_occurrence_metric_cohort_taint_labels",
            ),
            "trg_alert_metric_label_fence",
        ),
        (
            _INTEGRATION_MUTATION_FUNCTION,
            (
                "integration_delivery_metric_cohort_labels",
                "integration_delivery_metric_cohort_captured_labels",
                "integration_delivery_metric_cohort_taint_labels",
            ),
            "trg_integration_metric_label_fence",
        ),
    ):
        for index, table in enumerate(tables):
            bind.execute(
                sa.text(f"DROP TRIGGER {trigger_prefix}_{index}_v1 ON {table}")
            )
        bind.execute(sa.text(f"DROP FUNCTION {function_name}()"))


def _install_feed_taint_functions(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {_ALERT_FEED_TAINT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO alert_occurrence_metric_cohort_taint_labels (
                        cohort_id, label_id
                    )
                    SELECT cohort.id, NEW.handling_label_id
                    FROM alert_occurrence_metric_cohorts AS cohort
                    WHERE cohort.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {_INTEGRATION_FEED_TAINT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO integration_delivery_metric_cohort_taint_labels (
                        cohort_id, label_id
                    )
                    SELECT feed.cohort_id, NEW.handling_label_id
                    FROM integration_delivery_metric_cohort_feeds AS feed
                    WHERE feed.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )


def _restore_legacy_feed_taint_functions(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {_ALERT_FEED_TAINT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO alert_occurrence_metric_cohort_labels (
                        cohort_id, label_id
                    )
                    SELECT cohort.id, NEW.handling_label_id
                    FROM alert_occurrence_metric_cohorts AS cohort
                    WHERE cohort.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {_INTEGRATION_FEED_TAINT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.handling_label_id IS DISTINCT FROM OLD.handling_label_id THEN
                    INSERT INTO integration_delivery_metric_cohort_labels (
                        cohort_id, label_id
                    )
                    SELECT feed.cohort_id, NEW.handling_label_id
                    FROM integration_delivery_metric_cohort_feeds AS feed
                    WHERE feed.source_feed_id_snapshot = NEW.id
                    ON CONFLICT DO NOTHING;
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
