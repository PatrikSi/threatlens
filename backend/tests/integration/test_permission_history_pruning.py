from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from queue import Queue
import time
import uuid

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.alert_occurrence import (
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.audit_log import (
    AuditLog,
    AuditLogDataAccessFeed,
    AuditLogDataAccessLabel,
)
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.integration import (
    IntegrationDelivery,
    IntegrationInstance,
    IntegrationDeliveryMetric,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
    IntegrationDeliveryMetricCohortFeed,
)
from app.models.lifecycle_pruning import LifecyclePruningRecord
from app.services.audit_data_access import project_audit_logs
from app.services.data_access_policy import DataAccessContext
from app.services.data_policy_preflight import _audit_lineage_blockers
from app.services.alert_metric_data_policy import (
    alert_metric_cohort_integrity,
    alert_metric_policy_cohort_key,
    alert_metric_cohort_data_access_predicate,
    taint_alert_occurrence_metrics_for_feed,
)
from app.services.integration_metric_data_policy import (
    integration_metric_cohort_integrity,
    integration_metric_policy_cohort_key,
    integration_metric_cohort_data_access_predicate,
    taint_integration_delivery_metrics_for_feed,
)
from app.services.lifecycle_permission_pruning import (
    lock_permission_history_dependants,
    permission_pruning_candidates,
    prune_permission_history_parent,
)
from app.services.lifecycle_pruning_contracts import PruningContext
from app.services.alert_evaluation_admin import list_alert_occurrence_metrics
from app.services.integration_smtp_hooks import get_smtp_analytics
from app.services.integration_maintenance import rollup_terminal_integration_deliveries


OLD = datetime(2024, 1, 1, tzinfo=timezone.utc)
CUTOFF = OLD + timedelta(days=1)
METRICS = {
    "alert": (
        AlertOccurrenceMetric,
        AlertOccurrenceMetricCohort,
        AlertOccurrenceMetricCohortCapturedLabel,
        AlertOccurrenceMetricCohortLabel,
        AlertOccurrenceMetricCohortTaintLabel,
    ),
    "integration": (
        IntegrationDeliveryMetric,
        IntegrationDeliveryMetricCohort,
        IntegrationDeliveryMetricCohortCapturedLabel,
        IntegrationDeliveryMetricCohortLabel,
        IntegrationDeliveryMetricCohortTaintLabel,
    ),
}


def _access(mode="enforced"):
    return DataAccessContext(
        mode=mode,
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )


def _prune(db, model, parent_id, limit=1, *, eligible=True):
    timestamp = model.created_at if model is AuditLog else model.bucket_start
    predicate = timestamp < CUTOFF if eligible else timestamp > CUTOFF
    return prune_permission_history_parent(
        db,
        model=model,
        parent_id=parent_id,
        context=PruningContext(CUTOFF, predicate),
        limit=limit,
    )


def _audit(db, feeds=5):
    row = AuditLog(
        action="test.retention",
        resource_type="test",
        created_at=OLD,
        metadata_json={"secret": "restricted history"},
        data_access_governed=True,
        data_access_label_ids=[str(QUARANTINE_HANDLING_LABEL_ID)],
    )
    db.add(row)
    db.flush()
    db.add_all(
        AuditLogDataAccessFeed(
            audit_log_id=row.id, source_feed_id_snapshot=uuid.uuid4()
        )
        for _ in range(feeds)
    )
    db.flush()
    return row


def _metric(db, seed_users, kind, *, cohorts=3):
    model, cohort_model, captured, _, _ = METRICS[kind]
    db.execute(
        text(f"SELECT set_config('threatlens.{kind}_metric_cohort_write', 'on', true)")
    )
    if kind == "alert":
        parent = model(
            bucket_start=OLD,
            owner_user_id=seed_users["admin"].id,
            severity="high",
            lifecycle_state="new",
            suppressed=False,
            occurrence_count=cohorts,
        )
    else:
        instance = IntegrationInstance(
            name="Pruning SMTP", integration_type="smtp", direction="outbound"
        )
        db.add(instance)
        db.flush()
        parent = model(
            bucket_start=OLD,
            integration_id=instance.id,
            connector_type="smtp",
            event_type="rss_item_new",
            succeeded_count=cohorts,
        )
    db.add(parent)
    db.flush()
    rows = []
    for _ in range(cohorts):
        feed_id = uuid.uuid4()
        if kind == "alert":
            key = alert_metric_policy_cohort_key(
                policy_revision=1, label_ids=[QUARANTINE_HANDLING_LABEL_ID]
            )
            row = cohort_model(
                metric_id=parent.id,
                source_feed_id_snapshot=feed_id,
                policy_cohort_key=key,
                captured_policy_revision=1,
                provenance_complete=True,
                occurrence_count=1,
            )
        else:
            key = integration_metric_policy_cohort_key(
                policy_revision=1,
                provenance_complete=True,
                source_count=1,
                label_ids=[QUARANTINE_HANDLING_LABEL_ID],
                feed_ids=[feed_id],
            )
            row = cohort_model(
                metric_id=parent.id,
                policy_cohort_key=key,
                captured_policy_revision=1,
                provenance_complete=True,
                source_count=1,
                succeeded_count=1,
                failed_count=0,
                dead_letter_count=0,
                attempt_count=0,
                duration_total_ms=0,
                duration_max_ms=0,
            )
        db.add(row)
        db.flush()
        db.add(captured(cohort_id=row.id, label_id=QUARANTINE_HANDLING_LABEL_ID))
        if kind == "integration":
            db.add(
                IntegrationDeliveryMetricCohortFeed(
                    cohort_id=row.id, source_feed_id_snapshot=feed_id
                )
            )
        db.flush()
        rows.append((row.id, feed_id))
    return parent, rows


def _child_count(db, kind, parent_id):
    _, cohort, captured, effective, taint = METRICS[kind]
    cohort_ids = select(cohort.id).where(cohort.metric_id == parent_id)
    count = db.scalar(
        select(func.count()).select_from(cohort).where(cohort.metric_id == parent_id)
    )
    children = [captured, effective, taint]
    if kind == "integration":
        children.append(IntegrationDeliveryMetricCohortFeed)
    return count + sum(
        db.scalar(
            select(func.count())
            .select_from(child)
            .where(child.cohort_id.in_(cohort_ids))
        )
        for child in children
    )


@pytest.mark.parametrize("kind", METRICS)
def test_metric_provenance_drains_with_exact_committed_budget(
    db_session, seed_users, kind
):
    model = METRICS[kind][0]
    parent, _ = _metric(db_session, seed_users, kind)
    parent_id = parent.id
    remaining = _child_count(db_session, kind, parent_id)
    assert remaining >= 9
    assert permission_pruning_candidates(
        db_session, model=model, parent_ids=[parent_id], max_dependent_rows=1
    ) == {parent_id}
    for step in range(remaining):
        result = _prune(db_session, model, parent_id)
        assert result.children_pruned == 1
        assert result.parents_started == int(step == 0)
        db_session.commit()
        assert _child_count(db_session, kind, parent_id) == remaining - step - 1
        integrity = (
            alert_metric_cohort_integrity
            if kind == "alert"
            else integration_metric_cohort_integrity
        )
        assert integrity(db_session).valid
    db_session.execute(delete(model).where(model.id == parent_id))
    db_session.commit()
    assert (
        db_session.get(LifecyclePruningRecord, (model.__tablename__, parent_id)) is None
    )


@pytest.mark.parametrize("kind", METRICS)
def test_live_metric_labels_remain_immutable_and_pruning_requires_both_fences(
    db_session, seed_users, kind
):
    model, _, captured, _, _ = METRICS[kind]
    parent, rows = _metric(db_session, seed_users, kind, cohorts=1)
    for mark in (False, True):
        if mark:
            parent.retention_pruning_started_at = CUTOFF
            db_session.flush()
        with (
            pytest.raises(DBAPIError, match="provenance is immutable"),
            db_session.begin_nested(),
        ):
            db_session.execute(delete(captured).where(captured.cohort_id == rows[0][0]))
    # Setting a timestamp alone does not authorize provenance deletion.
    assert _prune(db_session, model, parent.id).children_pruned == 1


@pytest.mark.parametrize("kind", METRICS)
@pytest.mark.parametrize("mode", ["disabled", "audit", "enforced"])
def test_partial_metrics_are_hidden_in_every_policy_mode(
    db_session, seed_users, kind, mode
):
    model, cohort, _, _, _ = METRICS[kind]
    parent, rows = _metric(db_session, seed_users, kind, cohorts=1)
    predicate = (
        alert_metric_cohort_data_access_predicate
        if kind == "alert"
        else integration_metric_cohort_data_access_predicate
    )
    _prune(db_session, model, parent.id)
    db_session.commit()
    assert not list(
        db_session.scalars(
            select(cohort.id).where(cohort.id == rows[0][0], predicate(_access(mode)))
        )
    )
    if kind == "alert":
        response = list_alert_occurrence_metrics(
            db_session,
            owner_user_id=seed_users["admin"].id,
            data_access=_access(mode),
            since=OLD,
            until=CUTOFF,
            severities=[],
            lifecycle_states=[],
            suppressed=None,
            limit=100,
        )
        assert not response.items
    else:
        assert (
            get_smtp_analytics(db_session, data_access=_access(mode)).total_deliveries
            == 0
        )


@pytest.mark.parametrize("kind", METRICS)
def test_feed_taint_skips_pruning_metrics_and_cohort_mutations_are_rejected(
    db_session, seed_users, kind
):
    model, cohort, captured, effective, taint = METRICS[kind]
    parent, rows = _metric(db_session, seed_users, kind, cohorts=1)
    _prune(db_session, model, parent.id)
    db_session.commit()
    taint_feed = (
        taint_alert_occurrence_metrics_for_feed
        if kind == "alert"
        else taint_integration_delivery_metrics_for_feed
    )
    assert (
        taint_feed(
            db_session,
            feed_id=rows[0][1],
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        == 0
    )
    for child in (captured, effective, taint):
        db_session.execute(
            insert(child).values(
                cohort_id=rows[0][0], label_id=UNRESTRICTED_HANDLING_LABEL_ID
            )
        )
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(child)
                .where(
                    child.cohort_id == rows[0][0],
                    child.label_id == UNRESTRICTED_HANDLING_LABEL_ID,
                )
            )
            == 0
        )
    with (
        pytest.raises(DBAPIError, match="Expired history cleanup"),
        db_session.begin_nested(),
    ):
        db_session.execute(
            update(cohort)
            .where(cohort.id == rows[0][0])
            .values(policy_cohort_key="0" * 64)
        )


def test_audit_claim_rollback_and_eligibility_preserve_readable_provenance(db_session):
    row = _audit(db_session)
    row_id = row.id
    assert _prune(db_session, AuditLog, row_id, eligible=False).parents_started == 0
    with pytest.raises(RuntimeError), db_session.begin_nested():
        assert _prune(db_session, AuditLog, row_id).children_pruned == 1
        raise RuntimeError("transaction interrupted")
    db_session.expire_all()
    assert db_session.get(AuditLog, row_id).retention_pruning_started_at is None
    projection = project_audit_logs(db_session, [row], context=_access())
    assert projection.logs[0].data_access_redacted
    _prune(db_session, AuditLog, row_id, limit=6)
    db_session.commit()
    assert not project_audit_logs(db_session, [row], context=_access("disabled")).logs
    assert not _audit_lineage_blockers(db_session)


def test_oversized_audit_source_history_makes_bounded_progress_across_commits(
    db_session,
):
    row = _audit(db_session, feeds=0)
    row_id = row.id
    db_session.execute(
        text("""
        INSERT INTO audit_log_data_access_feeds (audit_log_id, source_feed_id_snapshot)
        SELECT :audit_id, md5(value::text)::uuid FROM generate_series(1, 20001) AS value
    """),
        {"audit_id": row_id},
    )
    db_session.commit()
    remaining = 20002
    while remaining:
        result = _prune(db_session, AuditLog, row_id, limit=4000)
        assert result.children_pruned == min(4000, remaining)
        remaining -= result.children_pruned
        db_session.commit()
        assert not project_audit_logs(
            db_session, [row], context=_access("disabled")
        ).logs
    assert (
        db_session.get(LifecyclePruningRecord, ("audit_logs", row_id)).children_pruned
        == 20002
    )
    assert not _audit_lineage_blockers(db_session)


def test_retained_audit_labels_and_feed_snapshots_cannot_be_mutated(db_session):
    row = _audit(db_session)
    for model in (AuditLogDataAccessLabel, AuditLogDataAccessFeed):
        with (
            pytest.raises(DBAPIError, match="provenance is immutable"),
            db_session.begin_nested(),
        ):
            db_session.execute(delete(model).where(model.audit_log_id == row.id))


@pytest.fixture
def committed_audit(database_engine):
    with Session(database_engine) as db:
        row = _audit(db)
        row_id = row.id
        db.commit()
        try:
            yield db, row_id
        finally:
            db.rollback()
            db.execute(delete(AuditLog).where(AuditLog.id == row_id))
            db.commit()


def test_previously_loaded_audit_cannot_be_projected_after_provenance_pruning(
    committed_audit, database_engine
):
    writer, row_id = committed_audit
    with Session(database_engine) as reader:
        stale = reader.get(AuditLog, row_id)
        assert stale.retention_pruning_started_at is None
        _prune(writer, AuditLog, row_id, limit=6)
        writer.commit()
        assert not project_audit_logs(reader, [stale], context=_access("disabled")).logs
        assert not project_audit_logs(reader, [stale], context=_access()).logs


def test_audit_projection_holds_parent_until_normalized_labels_are_consumed(
    committed_audit, database_engine
):
    reader, row_id = committed_audit
    row = reader.get(AuditLog, row_id)
    assert (
        project_audit_logs(reader, [row], context=_access())
        .logs[0]
        .data_access_redacted
    )
    pids = Queue()

    def prune_after_reader():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            pids.put(db.scalar(text("SELECT pg_backend_pid()")))
            result = _prune(db, AuditLog, row_id)
            db.commit()
            return result.children_pruned

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(prune_after_reader)
        pid = pids.get(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if reader.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Pruner did not wait for audit projection")
        reader.commit()
        assert future.result(timeout=5) == 1


def test_late_audit_taint_waits_for_claim_then_skips_hidden_history(
    committed_audit, database_engine
):
    pruner, row_id = committed_audit
    _prune(pruner, AuditLog, row_id)
    pids = Queue()

    def insert_taint_after_claim():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            pids.put(db.scalar(text("SELECT pg_backend_pid()")))
            db.execute(
                insert(AuditLogDataAccessLabel).values(
                    audit_log_id=row_id,
                    label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                )
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(insert_taint_after_claim)
        pid = pids.get(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if pruner.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Taint writer did not reach the source fence")
        pruner.commit()
        future.result(timeout=5)
    assert (
        pruner.scalar(
            select(func.count())
            .select_from(AuditLogDataAccessLabel)
            .where(
                AuditLogDataAccessLabel.audit_log_id == row_id,
                AuditLogDataAccessLabel.label_id == UNRESTRICTED_HANDLING_LABEL_ID,
            )
        )
        == 0
    )


def test_final_metric_cleanup_skips_a_cohort_writer_waiting_for_its_parent(
    database_engine,
):
    with Session(database_engine) as cleaner:
        metric, cohorts = _metric(cleaner, {}, "integration", cohorts=1)
        metric_id, instance_id = metric.id, metric.integration_id
        cleaner.commit()
        try:
            cleaner.scalar(
                select(IntegrationDeliveryMetric.id)
                .where(IntegrationDeliveryMetric.id == metric_id)
                .with_for_update()
            )
            pids = Queue()

            def update_existing_cohort():
                with Session(database_engine) as db:
                    db.execute(text("SET LOCAL statement_timeout = '5s'"))
                    pids.put(db.scalar(text("SELECT pg_backend_pid()")))
                    db.execute(
                        update(IntegrationDeliveryMetricCohort)
                        .where(IntegrationDeliveryMetricCohort.id == cohorts[0][0])
                        .values(updated_at=CUTOFF)
                    )
                    db.commit()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(update_existing_cohort)
                pid = pids.get(timeout=3)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if cleaner.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pid},
                    ):
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError("Cohort writer did not reach the parent fence")
                assert (
                    lock_permission_history_dependants(
                        cleaner, model=IntegrationDeliveryMetric, parent_ids=[metric_id]
                    )
                    == []
                )
                cleaner.commit()
                future.result(timeout=5)
            assert cleaner.get(IntegrationDeliveryMetric, metric_id) is not None
        finally:
            cleaner.rollback()
            cleaner.execute(
                delete(IntegrationInstance).where(IntegrationInstance.id == instance_id)
            )
            cleaner.commit()


def test_delayed_rollup_skips_claimed_expired_bucket_without_blocking_source_progress(
    db_session, seed_users
):
    parent, _ = _metric(db_session, seed_users, "integration", cohorts=1)
    parent_id, instance_id = parent.id, parent.integration_id
    _prune(db_session, IntegrationDeliveryMetric, parent_id)
    db_session.commit()
    delivery = IntegrationDelivery(
        integration_id=instance_id,
        connector_type="smtp",
        event_type="rss_item_new",
        state="succeeded",
        completed_at=OLD,
        created_at=OLD,
        updated_at=OLD,
        idempotency_key=f"expired-rollup-{uuid.uuid4()}",
        payload_json={},
    )
    db_session.add(delivery)
    db_session.commit()
    delivery_id = delivery.id
    assert (
        rollup_terminal_integration_deliveries(db_session, now=CUTOFF, batch_size=1)
        == 1
    )
    db_session.expire_all()
    assert (
        db_session.get(IntegrationDelivery, delivery_id).metrics_aggregated_at == CUTOFF
    )
    metric = db_session.get(IntegrationDeliveryMetric, parent_id)
    assert metric.retention_pruning_started_at is not None
    assert metric.succeeded_count == 1
