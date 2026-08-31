from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_data_access_context
from app.main import app
from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceActivity,
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.data_policy import (
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyConflict,
    DataPolicyMode,
    assign_feed_handling_label,
    set_handling_label_status,
)
from app.services.data_access_runtime import (
    ensure_alert_occurrence_data_access_envelope,
)
from app.services.alert_maintenance import maintain_alert_history
from app.services.alert_evaluation import persist_alert_evaluation_intent
from app.services.alert_evaluation_admin import list_alert_occurrence_metrics
from app.services.alert_metric_data_policy import alert_metric_cohort_integrity
from app.schemas.data_policy import HandlingLabelStatusRequest


@dataclass(frozen=True)
class AlertPolicySeed:
    rule: AlertInterest
    visible_feed: Feed
    restricted_feed: Feed
    visible_item: Item
    restricted_item: Item
    visible_occurrence: AlertOccurrence
    restricted_occurrence: AlertOccurrence


def _data_access_context(mode: DataPolicyMode) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
    )


@contextmanager
def _override_data_access(mode: DataPolicyMode) -> Iterator[None]:
    previous = app.dependency_overrides.get(get_data_access_context)
    context = _data_access_context(mode)
    app.dependency_overrides[get_data_access_context] = lambda: context
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_data_access_context, None)
        else:
            app.dependency_overrides[get_data_access_context] = previous


def _seed_alert_policy_data(db: Session, owner: User) -> AlertPolicySeed:
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    visible_feed = Feed(
        name=f"Visible alert policy feed {uuid.uuid4()}",
        url=f"https://example.com/visible-alert-policy-{uuid.uuid4()}.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name=f"Restricted alert policy feed {uuid.uuid4()}",
        url=f"https://example.com/restricted-alert-policy-{uuid.uuid4()}.xml",
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db.add_all([visible_feed, restricted_feed])
    db.flush()

    visible_item = _item(
        visible_feed,
        key="visible-alert-policy",
        observed_at=observed_at,
    )
    restricted_item = _item(
        restricted_feed,
        key="restricted-alert-policy",
        observed_at=observed_at + timedelta(seconds=1),
    )
    rule = AlertInterest(
        user_id=owner.id,
        name="Alert policy rule",
        category="threat",
        keywords=["policy-signal"],
        enabled=True,
        severity="high",
        revision=1,
        durable_since=observed_at - timedelta(minutes=1),
    )
    db.add_all([visible_item, restricted_item, rule])
    db.flush()

    visible_occurrence = _occurrence(owner, rule, visible_item)
    restricted_occurrence = _occurrence(owner, rule, restricted_item)
    db.add_all([visible_occurrence, restricted_occurrence])
    db.flush()
    db.add_all(
        [
            AlertOccurrenceActivity(
                occurrence_id=visible_occurrence.id,
                action="created",
                details_json={},
            ),
            AlertOccurrenceActivity(
                occurrence_id=restricted_occurrence.id,
                action="created",
                details_json={},
            ),
        ]
    )
    ensure_alert_occurrence_data_access_envelope(
        db,
        occurrence_id=visible_occurrence.id,
    )
    ensure_alert_occurrence_data_access_envelope(
        db,
        occurrence_id=restricted_occurrence.id,
    )
    db.commit()
    return AlertPolicySeed(
        rule=rule,
        visible_feed=visible_feed,
        restricted_feed=restricted_feed,
        visible_item=visible_item,
        restricted_item=restricted_item,
        visible_occurrence=visible_occurrence,
        restricted_occurrence=restricted_occurrence,
    )


def _item(feed: Feed, *, key: str, observed_at: datetime) -> Item:
    return Item(
        feed_id=feed.id,
        source_guid=key,
        url=f"https://{key}.example/article",
        canonical_url=f"https://{key}.example/article",
        title=f"Policy-signal item {key}",
        summary="A policy-signal alert candidate.",
        published_at=observed_at,
        first_seen_at=observed_at,
        dedupe_key=f"alert-policy:{key}:{uuid.uuid4()}",
        content_hash=hashlib.sha256(key.encode()).hexdigest(),
        status="content_fetched",
    )


def _occurrence(
    owner: User,
    rule: AlertInterest,
    item: Item,
) -> AlertOccurrence:
    return AlertOccurrence(
        alert_interest_id=rule.id,
        rule_id_snapshot=rule.id,
        owner_user_id=owner.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=rule.revision,
        item_content_hash=item.content_hash,
        alert_name_snapshot=rule.name,
        alert_category_snapshot=rule.category,
        alert_keywords_snapshot=list(rule.keywords),
        matched_keywords=["policy-signal"],
        source_snapshot_json={"item": {"id": str(item.id), "title": item.title}},
        severity_snapshot=rule.severity,
    )


def _not_found_signature(response) -> tuple[int, str, str]:
    payload = response.json()
    return response.status_code, payload["detail"], payload["error"]["code"]


def test_enforced_occurrence_reads_and_writes_hide_restricted_records(
    client: TestClient,
    auth_headers,
    db_session: Session,
    seed_users,
):
    seeded = _seed_alert_policy_data(db_session, seed_users["viewer"])
    missing_id = uuid.uuid4()

    with _override_data_access("enforced"):
        listed = client.get(
            "/alerts/occurrences",
            headers=auth_headers["viewer"],
        )
        visible_detail = client.get(
            f"/alerts/occurrences/{seeded.visible_occurrence.id}",
            headers=auth_headers["viewer"],
        )
        hidden_detail = client.get(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}",
            headers=auth_headers["viewer"],
        )
        missing_detail = client.get(
            f"/alerts/occurrences/{missing_id}",
            headers=auth_headers["viewer"],
        )
        hidden_activity = client.get(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}/activity",
            headers=auth_headers["viewer"],
        )
        missing_activity = client.get(
            f"/alerts/occurrences/{missing_id}/activity",
            headers=auth_headers["viewer"],
        )
        hidden_lifecycle = client.patch(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}/lifecycle",
            json={"expected_version": 1, "state": "acknowledged"},
            headers=auth_headers["viewer"],
        )
        missing_lifecycle = client.patch(
            f"/alerts/occurrences/{missing_id}/lifecycle",
            json={"expected_version": 1, "state": "acknowledged"},
            headers=auth_headers["viewer"],
        )
        snoozed_until = datetime.now(timezone.utc) + timedelta(hours=1)
        hidden_snooze = client.patch(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}/snooze",
            json={
                "expected_version": 1,
                "snoozed_until": snoozed_until.isoformat(),
                "reason": "Must remain inaccessible",
            },
            headers=auth_headers["viewer"],
        )
        missing_snooze = client.patch(
            f"/alerts/occurrences/{missing_id}/snooze",
            json={
                "expected_version": 1,
                "snoozed_until": snoozed_until.isoformat(),
                "reason": "Must remain inaccessible",
            },
            headers=auth_headers["viewer"],
        )
        hidden_bulk = client.post(
            "/alerts/occurrences/bulk/acknowledge",
            json={
                "items": [
                    {
                        "occurrence_id": str(seeded.visible_occurrence.id),
                        "expected_version": 1,
                    },
                    {
                        "occurrence_id": str(seeded.restricted_occurrence.id),
                        "expected_version": 1,
                    },
                ]
            },
            headers=auth_headers["viewer"],
        )
        missing_bulk = client.post(
            "/alerts/occurrences/bulk/acknowledge",
            json={
                "items": [
                    {
                        "occurrence_id": str(seeded.visible_occurrence.id),
                        "expected_version": 1,
                    },
                    {"occurrence_id": str(missing_id), "expected_version": 1},
                ]
            },
            headers=auth_headers["viewer"],
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert [row["id"] for row in listed.json()["items"]] == [
        str(seeded.visible_occurrence.id)
    ]
    assert visible_detail.status_code == 200
    assert _not_found_signature(hidden_detail) == _not_found_signature(missing_detail)
    assert _not_found_signature(hidden_activity) == _not_found_signature(
        missing_activity
    )
    assert _not_found_signature(hidden_lifecycle) == _not_found_signature(
        missing_lifecycle
    )
    assert _not_found_signature(hidden_snooze) == _not_found_signature(missing_snooze)
    assert _not_found_signature(hidden_bulk) == _not_found_signature(missing_bulk)

    db_session.expire_all()
    visible = db_session.get(AlertOccurrence, seeded.visible_occurrence.id)
    restricted = db_session.get(AlertOccurrence, seeded.restricted_occurrence.id)
    assert visible is not None and restricted is not None
    assert (visible.lifecycle_state, visible.version) == ("new", 1)
    assert (restricted.lifecycle_state, restricted.version) == ("new", 1)
    assert restricted.snoozed_until is None
    activity_count = db_session.scalar(
        select(func.count(AlertOccurrenceActivity.id)).where(
            AlertOccurrenceActivity.occurrence_id.in_(
                [seeded.visible_occurrence.id, seeded.restricted_occurrence.id]
            )
        )
    )
    assert activity_count == 2


def test_enforced_matches_and_backfill_filter_and_recheck_feed_labels(
    client: TestClient,
    auth_headers,
    db_session: Session,
    seed_users,
):
    seeded = _seed_alert_policy_data(db_session, seed_users["viewer"])
    window = {
        "since": (seeded.visible_item.first_seen_at - timedelta(minutes=1)).isoformat(),
        "until": (
            seeded.restricted_item.first_seen_at + timedelta(minutes=1)
        ).isoformat(),
        "limit": 10,
    }

    with _override_data_access("enforced"):
        matches = client.get("/alerts/matches", headers=auth_headers["viewer"])
        rule_preview = client.post(
            "/alerts/preview",
            json={
                "name": "Policy preview",
                "category": "threat",
                "keywords": ["policy-signal"],
                "limit": 10,
            },
            headers=auth_headers["viewer"],
        )
        backfill_preview = client.post(
            "/alerts/occurrences/reconciliation/preview",
            json=window,
            headers=auth_headers["admin"],
        )
        assert backfill_preview.status_code == 200, backfill_preview.text

        seeded.visible_feed.handling_label_id = QUARANTINE_HANDLING_LABEL_ID
        db_session.add(seeded.visible_feed)
        db_session.commit()
        applied = client.post(
            "/alerts/occurrences/reconciliation/apply",
            json={"preview_token": backfill_preview.json()["preview_token"]},
            headers=auth_headers["admin"],
        )

    for response in (matches, rule_preview):
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert [row["id"] for row in response.json()["items"]] == [
            str(seeded.visible_item.id)
        ]
    preview_payload = backfill_preview.json()
    assert preview_payload["matched_count"] == 1
    assert preview_payload["returned_count"] == 1
    assert [row["item_id"] for row in preview_payload["candidates"]] == [
        str(seeded.visible_item.id)
    ]
    assert applied.status_code == 202, applied.text
    assert applied.json()["accepted"] == 0
    assert applied.json()["skipped"] == 1
    request_count = db_session.scalar(
        select(func.count(AlertEvaluationRequest.id)).where(
            AlertEvaluationRequest.item_id.in_(
                [seeded.visible_item.id, seeded.restricted_item.id]
            )
        )
    )
    assert request_count == 0


def test_alert_metrics_preserve_policy_after_occurrence_rollup(
    client: TestClient,
    auth_headers,
    db_session: Session,
    seed_users,
):
    seeded = _seed_alert_policy_data(db_session, seed_users["viewer"])
    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(days=10)
    for occurrence in (
        seeded.visible_occurrence,
        seeded.restricted_occurrence,
    ):
        occurrence.created_at = observed_at
        occurrence.updated_at = observed_at
        occurrence.lifecycle_state = "closed"
        occurrence.closed_at = observed_at + timedelta(hours=1)
        occurrence.closure_disposition = "true_positive"
        db_session.add(occurrence)
    db_session.commit()

    maintained = maintain_alert_history(
        db_session,
        now=now,
        occurrence_retention_days=1,
        metric_retention_days=730,
    )
    assert maintained.occurrences_aggregated == 2
    assert maintained.occurrences_deleted == 2
    assert set(
        db_session.scalars(select(AlertOccurrenceMetricCohortLabel.label_id)).all()
    ) == {
        UNRESTRICTED_HANDLING_LABEL_ID,
        QUARANTINE_HANDLING_LABEL_ID,
    }

    with _override_data_access("enforced"):
        enforced = client.get(
            "/alerts/occurrences/metrics",
            headers=auth_headers["viewer"],
        )
    with _override_data_access("audit"):
        audit = client.get(
            "/alerts/occurrences/metrics",
            headers=auth_headers["viewer"],
        )

    assert enforced.status_code == 200, enforced.text
    assert sum(row["occurrence_count"] for row in enforced.json()["items"]) == 1
    assert audit.status_code == 200, audit.text
    assert sum(row["occurrence_count"] for row in audit.json()["items"]) == 2

    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    visible_cohort = db_session.scalar(
        select(AlertOccurrenceMetricCohort).where(
            AlertOccurrenceMetricCohort.source_feed_id_snapshot
            == seeded.visible_feed.id
        )
    )
    assert visible_cohort is not None
    visible_cohort_key = visible_cohort.policy_cohort_key
    assert set(
        db_session.scalars(
            select(AlertOccurrenceMetricCohortCapturedLabel.label_id).where(
                AlertOccurrenceMetricCohortCapturedLabel.cohort_id == visible_cohort.id
            )
        ).all()
    ) == {UNRESTRICTED_HANDLING_LABEL_ID}
    assert not db_session.scalars(
        select(AlertOccurrenceMetricCohortTaintLabel.label_id).where(
            AlertOccurrenceMetricCohortTaintLabel.cohort_id == visible_cohort.id
        )
    ).all()
    assign_feed_handling_label(
        db_session,
        feed_id=seeded.visible_feed.id,
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()
    visible_cohort_id = db_session.scalar(
        select(AlertOccurrenceMetricCohort.id).where(
            AlertOccurrenceMetricCohort.source_feed_id_snapshot
            == seeded.visible_feed.id
        )
    )
    assert visible_cohort_id is not None
    db_session.refresh(visible_cohort)
    assert visible_cohort.policy_cohort_key == visible_cohort_key
    assert set(
        db_session.scalars(
            select(AlertOccurrenceMetricCohortLabel.label_id).where(
                AlertOccurrenceMetricCohortLabel.cohort_id == visible_cohort_id
            )
        ).all()
    ) == {
        UNRESTRICTED_HANDLING_LABEL_ID,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert set(
        db_session.scalars(
            select(AlertOccurrenceMetricCohortCapturedLabel.label_id).where(
                AlertOccurrenceMetricCohortCapturedLabel.cohort_id == visible_cohort_id
            )
        ).all()
    ) == {UNRESTRICTED_HANDLING_LABEL_ID}
    assert set(
        db_session.scalars(
            select(AlertOccurrenceMetricCohortTaintLabel.label_id).where(
                AlertOccurrenceMetricCohortTaintLabel.cohort_id == visible_cohort_id
            )
        ).all()
    ) == {QUARANTINE_HANDLING_LABEL_ID}

    assign_feed_handling_label(
        db_session,
        feed_id=seeded.visible_feed.id,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()
    assert set(
        db_session.scalars(
            select(AlertOccurrenceMetricCohortTaintLabel.label_id).where(
                AlertOccurrenceMetricCohortTaintLabel.cohort_id == visible_cohort_id
            )
        ).all()
    ) == {
        UNRESTRICTED_HANDLING_LABEL_ID,
        QUARANTINE_HANDLING_LABEL_ID,
    }
    assert alert_metric_cohort_integrity(db_session).valid is True
    with pytest.raises(DBAPIError, match="provenance is immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "DELETE FROM alert_occurrence_metric_cohort_labels "
                    "WHERE cohort_id = :cohort_id AND label_id = :label_id"
                ),
                {
                    "cohort_id": visible_cohort_id,
                    "label_id": QUARANTINE_HANDLING_LABEL_ID,
                },
            )
    visible_cohort.policy_cohort_key = "0" * 64
    db_session.add(visible_cohort)
    db_session.flush()
    corrupted = alert_metric_cohort_integrity(db_session)
    assert corrupted.invalid_identity_count == 1
    assert corrupted.valid is False
    with _override_data_access("enforced"):
        corrupted_read = client.get(
            "/alerts/occurrences/metrics",
            headers=auth_headers["viewer"],
        )
    assert corrupted_read.status_code == 200, corrupted_read.text
    assert corrupted_read.json()["items"] == []
    db_session.rollback()
    with _override_data_access("enforced"):
        relabeled = client.get(
            "/alerts/occurrences/metrics",
            headers=auth_headers["viewer"],
        )
    assert relabeled.status_code == 200, relabeled.text
    assert relabeled.json()["items"] == []


def test_alert_metric_rollup_preserves_counts_across_feed_label_transition(
    db_session: Session,
    seed_users,
):
    owner = seed_users["viewer"]
    label_a = HandlingLabel(
        key=f"metric-a-{uuid.uuid4().hex}",
        name="Metric transition A",
        created_by_user_id=seed_users["admin"].id,
    )
    label_b = HandlingLabel(
        key=f"metric-b-{uuid.uuid4().hex}",
        name="Metric transition B",
        created_by_user_id=seed_users["admin"].id,
    )
    db_session.add_all([label_a, label_b])
    db_session.flush()
    feed = Feed(
        name=f"Metric transition feed {uuid.uuid4()}",
        url=f"https://example.com/metric-transition-{uuid.uuid4()}.xml",
        handling_label_id=label_a.id,
    )
    rule = AlertInterest(
        user_id=owner.id,
        name="Metric transition rule",
        category="threat",
        keywords=["transition"],
        enabled=True,
        severity="high",
        revision=1,
    )
    db_session.add_all([feed, rule])
    db_session.flush()

    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(days=10)
    item_a = _item(feed, key="metric-transition-a", observed_at=observed_at)
    db_session.add(item_a)
    db_session.flush()
    occurrence_a = _occurrence(owner, rule, item_a)
    db_session.add(occurrence_a)
    db_session.flush()
    ensure_alert_occurrence_data_access_envelope(
        db_session,
        occurrence_id=occurrence_a.id,
    )
    db_session.commit()

    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=label_b.id,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    item_b = _item(
        feed,
        key="metric-transition-b",
        observed_at=observed_at + timedelta(minutes=1),
    )
    db_session.add(item_b)
    db_session.flush()
    occurrence_b = _occurrence(owner, rule, item_b)
    db_session.add(occurrence_b)
    db_session.flush()
    ensure_alert_occurrence_data_access_envelope(
        db_session,
        occurrence_id=occurrence_b.id,
    )
    for occurrence in (occurrence_a, occurrence_b):
        occurrence.created_at = observed_at
        occurrence.updated_at = observed_at
        occurrence.lifecycle_state = "closed"
        occurrence.closed_at = observed_at + timedelta(hours=1)
        occurrence.closure_disposition = "true_positive"
        db_session.add(occurrence)
    db_session.commit()

    maintained = maintain_alert_history(
        db_session,
        now=now,
        occurrence_retention_days=1,
        metric_retention_days=730,
    )
    assert maintained.occurrences_aggregated == 2
    assert maintained.occurrences_deleted == 2

    def _metrics(allowed_label_ids: set[uuid.UUID]):
        return list_alert_occurrence_metrics(
            db_session,
            owner_user_id=owner.id,
            data_access=DataAccessContext(
                mode="enforced",
                policy_revision=state.revision,
                coverage_version=1,
                principal_type="user",
                principal_id=owner.id,
                principal_eligible=True,
                allowed_label_ids=frozenset(allowed_label_ids),
            ),
            since=observed_at - timedelta(days=1),
            until=observed_at + timedelta(days=1),
            severities=[],
            lifecycle_states=[],
            suppressed=None,
            limit=100,
        )

    label_b_only = _metrics({label_b.id})
    both_labels = _metrics({label_a.id, label_b.id})
    assert [row.occurrence_count for row in label_b_only.items] == [1]
    assert [row.occurrence_count for row in both_labels.items] == [2]
    assert label_b_only.items[0].id == both_labels.items[0].id

    metric = db_session.scalar(
        select(AlertOccurrenceMetric).where(
            AlertOccurrenceMetric.owner_user_id == owner.id
        )
    )
    assert metric is not None
    assert metric.occurrence_count == 2
    assert (
        db_session.scalar(
            select(func.count(AlertOccurrenceMetricCohort.id)).where(
                AlertOccurrenceMetricCohort.metric_id == metric.id
            )
        )
        == 2
    )

    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=label_a.id,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )
    item_c = _item(
        feed,
        key="metric-transition-c",
        observed_at=observed_at + timedelta(minutes=2),
    )
    db_session.add(item_c)
    db_session.flush()
    occurrence_c = _occurrence(owner, rule, item_c)
    occurrence_c.created_at = observed_at
    occurrence_c.updated_at = observed_at
    occurrence_c.lifecycle_state = "closed"
    occurrence_c.closed_at = observed_at + timedelta(hours=1)
    occurrence_c.closure_disposition = "true_positive"
    db_session.add(occurrence_c)
    db_session.flush()
    ensure_alert_occurrence_data_access_envelope(
        db_session,
        occurrence_id=occurrence_c.id,
    )
    db_session.commit()
    maintained_again = maintain_alert_history(
        db_session,
        now=now,
        occurrence_retention_days=1,
        metric_retention_days=730,
    )
    assert maintained_again.occurrences_aggregated == 1
    assert maintained_again.occurrences_deleted == 1

    label_a_only = _metrics({label_a.id})
    label_b_only_after_reuse = _metrics({label_b.id})
    both_labels_after_reuse = _metrics({label_a.id, label_b.id})
    assert [row.occurrence_count for row in label_a_only.items] == [1]
    assert label_b_only_after_reuse.items == []
    assert [row.occurrence_count for row in both_labels_after_reuse.items] == [3]
    db_session.refresh(metric)
    assert metric.occurrence_count == 3
    assert (
        db_session.scalar(
            select(func.count(AlertOccurrenceMetricCohort.id)).where(
                AlertOccurrenceMetricCohort.metric_id == metric.id
            )
        )
        == 3
    )

    assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=label_b.id,
        expected_policy_revision=state.revision,
        actor_user_id=seed_users["admin"].id,
    )

    with pytest.raises(DataPolicyConflict, match="derived intelligence"):
        set_handling_label_status(
            db_session,
            label_id=label_a.id,
            payload=HandlingLabelStatusRequest(
                expected_revision=label_a.revision,
                active=False,
            ),
            actor_user_id=seed_users["admin"].id,
        )


def test_alert_evaluation_operations_hide_restricted_source_items(
    client: TestClient,
    auth_headers,
    db_session: Session,
    seed_users,
):
    seeded = _seed_alert_policy_data(db_session, seed_users["viewer"])
    visible_intent = persist_alert_evaluation_intent(
        db_session,
        item=seeded.visible_item,
    )
    restricted_intent = persist_alert_evaluation_intent(
        db_session,
        item=seeded.restricted_item,
    )
    restricted_request = db_session.get(
        AlertEvaluationRequest,
        restricted_intent.request_id,
    )
    assert restricted_request is not None
    restricted_request.state = "dead_letter"
    restricted_request.completed_at = datetime.now(timezone.utc)
    restricted_request.last_error_code = "test_failure"
    restricted_request.last_error_message = "Restricted test failure."
    db_session.add(restricted_request)
    db_session.commit()
    missing_id = uuid.uuid4()

    with _override_data_access("enforced"):
        listed = client.get(
            "/alerts/occurrences/evaluations",
            headers=auth_headers["admin"],
        )
        hidden_detail = client.get(
            f"/alerts/occurrences/evaluations/{restricted_intent.request_id}",
            headers=auth_headers["admin"],
        )
        missing_detail = client.get(
            f"/alerts/occurrences/evaluations/{missing_id}",
            headers=auth_headers["admin"],
        )
        hidden_activity = client.get(
            f"/alerts/occurrences/evaluations/{restricted_intent.request_id}/activity",
            headers=auth_headers["admin"],
        )
        missing_activity = client.get(
            f"/alerts/occurrences/evaluations/{missing_id}/activity",
            headers=auth_headers["admin"],
        )
        hidden_replay = client.post(
            f"/alerts/occurrences/evaluations/{restricted_intent.request_id}/replay",
            json={"expected_version": restricted_request.version},
            headers=auth_headers["admin"],
        )
        missing_replay = client.post(
            f"/alerts/occurrences/evaluations/{missing_id}/replay",
            json={"expected_version": 1},
            headers=auth_headers["admin"],
        )

    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert [row["id"] for row in listed.json()["items"]] == [
        str(visible_intent.request_id)
    ]
    assert _not_found_signature(hidden_detail) == _not_found_signature(missing_detail)
    assert _not_found_signature(hidden_activity) == _not_found_signature(
        missing_activity
    )
    assert _not_found_signature(hidden_replay) == _not_found_signature(missing_replay)
    db_session.expire_all()
    unchanged = db_session.get(AlertEvaluationRequest, restricted_intent.request_id)
    assert unchanged is not None
    assert unchanged.state == "dead_letter"
    assert unchanged.version == restricted_request.version


@pytest.mark.parametrize("mode", ["disabled", "audit"])
def test_non_enforced_alert_policy_preserves_read_and_write_behavior(
    mode: DataPolicyMode,
    client: TestClient,
    auth_headers,
    db_session: Session,
    seed_users,
):
    seeded = _seed_alert_policy_data(db_session, seed_users["viewer"])
    window = {
        "since": (seeded.visible_item.first_seen_at - timedelta(minutes=1)).isoformat(),
        "until": (
            seeded.restricted_item.first_seen_at + timedelta(minutes=1)
        ).isoformat(),
        "limit": 10,
    }

    with _override_data_access(mode):
        occurrences = client.get(
            "/alerts/occurrences",
            headers=auth_headers["viewer"],
        )
        matches = client.get("/alerts/matches", headers=auth_headers["viewer"])
        restricted_detail = client.get(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}",
            headers=auth_headers["viewer"],
        )
        lifecycle = client.patch(
            f"/alerts/occurrences/{seeded.restricted_occurrence.id}/lifecycle",
            json={"expected_version": 1, "state": "acknowledged"},
            headers=auth_headers["viewer"],
        )
        preview = client.post(
            "/alerts/occurrences/reconciliation/preview",
            json=window,
            headers=auth_headers["admin"],
        )
        assert preview.status_code == 200, preview.text
        applied = client.post(
            "/alerts/occurrences/reconciliation/apply",
            json={"preview_token": preview.json()["preview_token"]},
            headers=auth_headers["admin"],
        )

    assert occurrences.status_code == 200
    assert occurrences.json()["total"] == 2
    assert matches.status_code == 200
    assert matches.json()["total"] == 2
    assert restricted_detail.status_code == 200
    assert lifecycle.status_code == 200
    assert lifecycle.json()["lifecycle_state"] == "acknowledged"
    assert preview.json()["matched_count"] == 2
    assert preview.json()["returned_count"] == 2
    assert applied.status_code == 202, applied.text
    assert applied.json()["accepted"] == 2
    assert applied.json()["skipped"] == 0
