from __future__ import annotations

import copy
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - importing the package must register every model
from app.db.base import Base
from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationEvent,
    IntegrationInstance,
)
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.notification_webhook import NotificationWebhook
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.user import User
from app.services.alert_evaluation import (
    ALERT_EVALUATION_DISPATCH_STALE_SECONDS,
    ALERT_EVALUATION_REPUBLISH_BASE_SECONDS,
    AlertBackfillPreviewError,
    claim_alert_evaluation_request,
    create_alert_backfill_preview,
    evaluate_alert_request,
    list_alert_backfill_candidates,
    persist_alert_backfill_intents,
    persist_alert_backfill_preview_intents,
    persist_alert_evaluation_intent,
    record_direct_alert_evaluation_publications,
    record_alert_evaluation_publications,
    release_failed_direct_alert_publications,
    reserve_recoverable_alert_evaluations,
)
from app.services.alert_maintenance import _delete_terminal_evaluation_ids
from app.services.integration_connectors.base import IntegrationEventContextError
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.integration_events import (
    alert_match_event_owner_ids,
    build_alert_match_snapshot_payload,
    delivery_payload_for_owner,
)
from app.services.notification_webhook_templates import AlertMatchContext
from app.tasks.alert_tasks import (
    dispatch_pending_alert_evaluations,
    enqueue_alert_evaluation_requests,
)


def _seed_target(db, user: User, *, suffix: str):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name=f"Adversarial feed {suffix}",
        url=f"https://example.com/adversarial-{suffix}.xml",
        enabled=True,
    )
    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid=f"adversarial-{suffix}",
        url=f"https://example.com/adversarial/{suffix}",
        canonical_url=f"https://example.com/adversarial/{suffix}",
        title=f"Fortinet exploitation {suffix}",
        summary="Researchers observed active exploitation.",
        first_seen_at=now,
        dedupe_key=f"adversarial-{suffix}",
        content_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        status="content_fetched",
    )
    rule = AlertInterest(
        id=uuid.uuid4(),
        user_id=user.id,
        name=f"Original rule {suffix}",
        category="threat",
        keywords=["fortinet"],
        enabled=True,
        severity="high",
        revision=1,
        durable_since=now,
    )
    classification = ItemClassification(
        item_id=item.id,
        primary_category="vulnerability",
        secondary_categories=[],
        confidence=0.9,
        scores_json={},
        matched_terms_json={},
        source_hash=hashlib.sha256(f"classification-{suffix}".encode()).hexdigest(),
        rules_version="adversarial",
    )
    db.add_all([feed, item, rule, classification])
    db.commit()
    return feed, item, rule, classification


def _evaluate(db, request_id: uuid.UUID):
    claim = claim_alert_evaluation_request(db, request_id=request_id)
    assert claim is not None
    db.commit()
    outcome = evaluate_alert_request(
        db,
        request_id=request_id,
        lease_token=claim.lease_token,
    )
    db.commit()
    return outcome


def test_accepted_rule_snapshot_survives_rule_edit_and_delete(db_session, seed_users):
    _feed, item, rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="immutable-acceptance",
    )
    rule_id = rule.id
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()

    accepted = db_session.scalar(
        select(AlertEvaluationMatch).where(
            AlertEvaluationMatch.request_id == intent.request_id
        )
    )
    assert accepted is not None
    assert accepted.alert_name_snapshot == rule.name
    assert accepted.rule_revision == 1
    assert accepted.matched_keywords == ["fortinet"]

    rule.name = "Mutated rule"
    rule.keywords = ["does-not-match"]
    rule.revision = 2
    rule.durable_since = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.add(rule)
    db_session.commit()
    db_session.delete(rule)
    db_session.commit()

    outcome = _evaluate(db_session, intent.request_id)
    occurrence = db_session.scalar(select(AlertOccurrence))
    event = db_session.scalar(select(IntegrationEvent))
    assert outcome.occurrences_created == 1
    assert occurrence is not None
    assert occurrence.alert_interest_id is None
    assert occurrence.rule_id_snapshot == rule_id
    assert occurrence.rule_revision == 1
    assert occurrence.alert_name_snapshot.startswith("Original rule")
    assert occurrence.matched_keywords == ["fortinet"]
    assert event is not None
    assert event.payload_json["alert"]["names"] == [occurrence.alert_name_snapshot]


@pytest.mark.parametrize("account_state", ["inactive", "unapproved"])
def test_accepted_match_materializes_after_owner_loses_notification_eligibility(
    db_session,
    seed_users,
    account_state,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix=f"owner-{account_state}-after-acceptance",
    )
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    assert (
        db_session.scalar(
            select(func.count(AlertEvaluationMatch.id)).where(
                AlertEvaluationMatch.request_id == intent.request_id
            )
        )
        == 1
    )

    owner = seed_users["viewer"]
    if account_state == "inactive":
        owner.is_active = False
    else:
        owner.is_approved = False
    db_session.add(owner)
    db_session.commit()

    outcome = _evaluate(db_session, intent.request_id)
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(
            AlertOccurrence.item_id_snapshot == item.id,
            AlertOccurrence.item_content_hash == item.content_hash,
        )
    )
    request = db_session.get(AlertEvaluationRequest, intent.request_id)

    assert outcome.occurrences_created == 1
    assert outcome.integration_event_ids == ()
    assert outcome.notifications_skipped == 1
    assert occurrence is not None
    assert occurrence.owner_user_id == owner.id
    assert occurrence.integration_event_id is None
    assert request is not None
    assert request.state == "succeeded"
    assert request.occurrence_count == 1
    skipped = db_session.scalar(
        select(AlertEvaluationRequestActivity).where(
            AlertEvaluationRequestActivity.request_id == intent.request_id,
            AlertEvaluationRequestActivity.action == "notification_skipped",
        )
    )
    succeeded = db_session.scalar(
        select(AlertEvaluationRequestActivity).where(
            AlertEvaluationRequestActivity.request_id == intent.request_id,
            AlertEvaluationRequestActivity.action == "succeeded",
        )
    )
    expected_reason = f"owner_{account_state}_after_acceptance"
    assert skipped is not None
    assert skipped.details_json == {
        "reason": expected_reason,
        "owner_user_id": str(owner.id),
        "stage": "post_acceptance",
    }
    assert succeeded is not None
    assert succeeded.details_json["notification_skip_count"] == 1
    assert succeeded.details_json["notification_skip_reasons"] == {expected_reason: 1}


def test_owner_rules_are_fully_paginated_without_silent_drops(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, healthy_rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="owner-quota",
    )
    now = item.first_seen_at
    noisy_owner = seed_users["analyst"]
    inactive_owner = User(
        id=uuid.uuid4(),
        email=f"inactive-alert-{uuid.uuid4().hex}@example.com",
        password_hash="x",
        role="viewer",
        is_active=False,
        is_approved=True,
    )
    unapproved_owner = User(
        id=uuid.uuid4(),
        email=f"unapproved-alert-{uuid.uuid4().hex}@example.com",
        password_hash="x",
        role="viewer",
        is_active=True,
        is_approved=False,
    )
    db_session.add_all([inactive_owner, unapproved_owner])
    db_session.flush()
    for owner, count, label in (
        (noisy_owner, 2, "noisy"),
        (inactive_owner, 1, "inactive"),
        (unapproved_owner, 1, "unapproved"),
    ):
        for index in range(count):
            db_session.add(
                AlertInterest(
                    user_id=owner.id,
                    name=f"{label}-{index}",
                    category="threat",
                    keywords=["fortinet"],
                    enabled=True,
                    severity="medium",
                    revision=1,
                    durable_since=now,
                )
            )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.alert_acceptance.ALERT_ACCEPTANCE_RULE_PAGE_SIZE",
        1,
    )

    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    accepted = list(
        db_session.scalars(
            select(AlertEvaluationMatch).where(
                AlertEvaluationMatch.request_id == intent.request_id
            )
        ).all()
    )
    assert request.accepted_rule_count == 3
    assert request.accepted_match_count == 3
    assert request.degraded_owner_count == 0
    assert request.degraded_owners_json == []
    assert healthy_rule.id in {match.alert_interest_id for match in accepted}
    assert {match.owner_user_id for match in accepted} == {
        healthy_rule.user_id,
        noisy_owner.id,
    }

    outcome = _evaluate(db_session, intent.request_id)
    occurrences = list(db_session.scalars(select(AlertOccurrence)).all())
    assert outcome.occurrences_created == 3
    assert {row.owner_user_id for row in occurrences} == {
        healthy_rule.user_id,
        noisy_owner.id,
    }


def test_more_than_one_thousand_matches_emit_one_bounded_owner_event(
    db_session,
    seed_users,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="single-owner-event-load-bound",
    )
    now = item.first_seen_at
    db_session.add_all(
        [
            AlertInterest(
                user_id=seed_users["viewer"].id,
                name=f"Load-bound rule {index}",
                category="threat",
                keywords=["fortinet"],
                enabled=True,
                severity="medium",
                revision=1,
                durable_since=now,
            )
            for index in range(1_005)
        ]
    )
    db_session.commit()

    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    outcome = _evaluate(db_session, intent.request_id)

    assert outcome.occurrences_created == 1_006
    assert len(outcome.integration_event_ids) == 1
    event = db_session.get(IntegrationEvent, outcome.integration_event_ids[0])
    assert event is not None
    assert event.payload_json["occurrence_count"] == 1_006
    assert event.payload_json["occurrence_ids_truncated"] is True
    assert len(event.payload_json["occurrence_ids"]) == 500
    assert event.payload_json["alert"]["count"] == 1_006
    assert len(event.payload_json["alert"]["names"]) == 100
    assert (
        db_session.scalar(
            select(func.count(IntegrationEvent.id)).where(
                IntegrationEvent.event_type == "alert_match"
            )
        )
        == 1
    )


def test_dispatch_claim_bounds_republication_of_a_delayed_queue(
    db_session,
    seed_users,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="delayed-queue",
    )
    accepted_at = datetime.now(timezone.utc)
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
        now=accepted_at,
    )
    db_session.commit()

    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=accepted_at,
    ).request_ids
    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=accepted_at
        + timedelta(seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS - 1),
    ).request_ids

    first_recovery_at = accepted_at + timedelta(
        seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS + 1
    )
    reservation = reserve_recoverable_alert_evaluations(
        db_session,
        now=first_recovery_at,
    )
    assert reservation.request_ids == (intent.request_id,)
    db_session.commit()

    release_failed_direct_alert_publications(
        db_session,
        request_ids=[intent.request_id],
    )
    db_session.commit()
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.dispatch_claimed_at == first_recovery_at
    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=first_recovery_at,
    ).request_ids

    next_recovery = reserve_recoverable_alert_evaluations(
        db_session,
        now=first_recovery_at
        + timedelta(seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS + 1),
    )
    assert next_recovery.request_ids == (intent.request_id,)
    assert (
        db_session.get(AlertEvaluationRequest, intent.request_id).dispatch_attempt_count
        == 3
    )


def test_direct_publish_failure_keeps_intent_and_releases_its_claim(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="direct-publish-failure",
    )
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()

    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr("app.tasks.alert_tasks.db_session", same_session)
    monkeypatch.setattr(
        "app.tasks.alert_tasks.process_alert_evaluation.delay",
        lambda _request_id: (_ for _ in ()).throw(RuntimeError("broker down")),
    )
    assert enqueue_alert_evaluation_requests([intent.request_id]) is False

    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request is not None
    assert request.state == "pending"
    assert request.dispatch_claimed_at is None
    assert request.dispatch_published_at is None


def test_successful_direct_publication_is_not_republished_while_pending(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="direct-publication-recorded",
    )
    accepted_at = datetime.now(timezone.utc)
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
        now=accepted_at,
    )
    db_session.commit()

    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr("app.tasks.alert_tasks.db_session", same_session)
    monkeypatch.setattr(
        "app.tasks.alert_tasks.process_alert_evaluation.delay",
        lambda _request_id: None,
    )

    assert enqueue_alert_evaluation_requests([intent.request_id]) is True
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.dispatch_published_at is not None
    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=accepted_at
        + timedelta(seconds=ALERT_EVALUATION_REPUBLISH_BASE_SECONDS - 1),
    ).request_ids

    lost_message_recovery = reserve_recoverable_alert_evaluations(
        db_session,
        now=request.dispatch_published_at
        + timedelta(seconds=ALERT_EVALUATION_REPUBLISH_BASE_SECONDS + 1),
    )
    assert lost_message_recovery.request_ids == (intent.request_id,)


def test_stale_processing_is_recoverable_after_a_recorded_publication(
    db_session,
    seed_users,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="published-stale-processing",
    )
    accepted_at = datetime.now(timezone.utc)
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
        now=accepted_at,
    )
    db_session.commit()
    published_at = accepted_at + timedelta(seconds=1)
    record_alert_evaluation_publications(
        db_session,
        request_ids=[intent.request_id],
        reserved_at=accepted_at,
        published_at=published_at,
    )
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    request.state = "processing"
    request.dispatch_claimed_at = None
    request.claimed_at = published_at
    request.lease_expires_at = published_at - timedelta(seconds=1)
    db_session.add(request)
    db_session.commit()

    recovered_at = published_at + timedelta(
        seconds=ALERT_EVALUATION_REPUBLISH_BASE_SECONDS + 1
    )
    reservation = reserve_recoverable_alert_evaluations(
        db_session,
        now=recovered_at,
    )

    assert reservation.request_ids == (intent.request_id,)
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.dispatch_claimed_at == recovered_at
    assert request.dispatch_published_at is None

    republished_at = recovered_at + timedelta(seconds=1)
    record_alert_evaluation_publications(
        db_session,
        request_ids=[intent.request_id],
        reserved_at=recovered_at,
        published_at=republished_at,
    )
    db_session.commit()
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.dispatch_published_at == republished_at
    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=republished_at + timedelta(seconds=1),
    ).request_ids


def test_worker_claim_wins_race_with_publication_marker_recording(
    db_session,
    seed_users,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="publication-worker-race",
    )
    accepted_at = datetime.now(timezone.utc)
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
        now=accepted_at,
    )
    db_session.commit()

    claim = claim_alert_evaluation_request(
        db_session,
        request_id=intent.request_id,
        now=accepted_at + timedelta(seconds=1),
    )
    assert claim is not None
    record_direct_alert_evaluation_publications(
        db_session,
        request_ids=[intent.request_id],
        published_at=accepted_at + timedelta(seconds=2),
    )

    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.state == "processing"
    assert request.dispatch_claimed_at is None
    assert request.dispatch_published_at is None


def test_reconciliation_records_successful_queue_publication(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="reconciliation-publication-recorded",
    )
    accepted_at = datetime.now(timezone.utc)
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
        now=accepted_at,
    )
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    request.dispatch_claimed_at = accepted_at - timedelta(
        seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS + 1
    )
    db_session.add(request)
    db_session.commit()

    @contextmanager
    def same_session():
        yield db_session

    monkeypatch.setattr("app.tasks.alert_tasks.db_session", same_session)
    monkeypatch.setattr(
        "app.tasks.alert_tasks.process_alert_evaluation.delay",
        lambda _request_id: None,
    )

    result = dispatch_pending_alert_evaluations.run()

    assert result == {
        "status": "ok",
        "scanned": 1,
        "queued": 1,
        "enqueue_failed": False,
    }
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.dispatch_published_at is not None
    assert not reserve_recoverable_alert_evaluations(
        db_session,
        now=request.dispatch_published_at
        + timedelta(seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS + 1),
    ).request_ids


def test_backfill_cursor_progresses_across_equal_timestamps(db_session, seed_users):
    now = datetime.now(timezone.utc)
    feed = Feed(
        id=uuid.uuid4(),
        name="Cursor feed",
        url=f"https://example.com/cursor-{uuid.uuid4().hex}.xml",
        enabled=True,
    )
    db_session.add(feed)
    db_session.flush()
    db_session.add(
        AlertInterest(
            user_id=seed_users["viewer"].id,
            name="Cursor rule",
            category="threat",
            keywords=["fortinet"],
            enabled=True,
            severity="medium",
            revision=1,
            durable_since=now,
        )
    )
    item_ids: set[uuid.UUID] = set()
    for index in range(3):
        item = Item(
            id=uuid.uuid4(),
            feed_id=feed.id,
            source_guid=f"cursor-{index}-{uuid.uuid4().hex}",
            url=f"https://example.com/cursor/{index}/{uuid.uuid4().hex}",
            title=f"Fortinet cursor item {index}",
            first_seen_at=now,
            dedupe_key=f"cursor-{index}-{uuid.uuid4().hex}",
            content_hash=hashlib.sha256(f"cursor-{index}".encode()).hexdigest(),
            status="content_fetched",
        )
        item_ids.add(item.id)
        db_session.add(item)
    db_session.commit()

    first = list_alert_backfill_candidates(
        db_session,
        since=now - timedelta(seconds=1),
        until=now + timedelta(seconds=1),
        limit=2,
    )
    assert len(first.candidates) == 2
    assert first.truncated is True
    assert first.next_cursor_first_seen_at == now
    assert first.next_cursor_item_id is not None

    first_persisted = persist_alert_backfill_intents(
        db_session,
        since=now - timedelta(seconds=1),
        until=now + timedelta(seconds=1),
        limit=2,
    )
    db_session.commit()
    second = list_alert_backfill_candidates(
        db_session,
        since=now - timedelta(seconds=1),
        until=now + timedelta(seconds=1),
        limit=2,
        cursor_first_seen_at=first.next_cursor_first_seen_at,
        cursor_item_id=first.next_cursor_item_id,
    )
    second_persisted = persist_alert_backfill_intents(
        db_session,
        since=now - timedelta(seconds=1),
        until=now + timedelta(seconds=1),
        limit=2,
        cursor_first_seen_at=first.next_cursor_first_seen_at,
        cursor_item_id=first.next_cursor_item_id,
    )
    db_session.commit()

    first_ids = {candidate.item_id for candidate in first.candidates}
    second_ids = {candidate.item_id for candidate in second.candidates}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == item_ids
    assert second.truncated is False
    assert len(first_persisted.request_ids) == 2
    assert len(second_persisted.request_ids) == 1
    requests = list(db_session.scalars(select(AlertEvaluationRequest)).all())
    assert len(requests) == 3
    assert all(row.source == "backfill" and not row.notify for row in requests)


def test_backfill_candidate_count_and_page_share_one_database_snapshot(
    db_session,
    seed_users,
    monkeypatch,
):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="single-snapshot-preview",
    )
    since = item.first_seen_at - timedelta(seconds=1)
    until = item.first_seen_at + timedelta(seconds=1)
    original_execute = db_session.execute
    statements: list[str] = []

    def _execute(statement, *args, **kwargs):
        statements.append(str(statement))
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _execute)

    page = list_alert_backfill_candidates(
        db_session,
        since=since,
        until=until,
        limit=10,
    )

    assert page.matched_count == len(page.candidates) == 1
    assert len(statements) == 1
    assert "count(" in statements[0].lower()
    assert "over" in statements[0].lower()


def test_backfill_preview_is_owner_bound_expiring_single_use_and_content_stable(
    db_session,
    seed_users,
):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="stable-preview",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    db_session.commit()

    with pytest.raises(AlertBackfillPreviewError) as foreign_error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["analyst"].id,
            now=now,
        )
    assert foreign_error.value.code == "alert_backfill_preview_not_found"

    item.content_hash = "f" * 64
    db_session.add(item)
    db_session.commit()
    applied = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    db_session.commit()
    assert applied.request_ids == ()
    assert applied.skipped_count == 1
    assert applied.replayed is False

    replayed = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    assert replayed.request_ids == applied.request_ids
    assert replayed.existing_count == applied.existing_count
    assert replayed.skipped_count == applied.skipped_count
    assert replayed.replayed is True

    legacy_consumed = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    legacy_consumed.preview.consumed_at = now
    db_session.add(legacy_consumed.preview)
    db_session.commit()
    with pytest.raises(AlertBackfillPreviewError) as consumed_error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=legacy_consumed.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
    assert consumed_error.value.code == "alert_backfill_preview_consumed"

    expired = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    db_session.commit()
    with pytest.raises(AlertBackfillPreviewError) as expired_error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=expired.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now + timedelta(hours=1),
        )
    assert expired_error.value.code == "alert_backfill_preview_expired"


@pytest.mark.parametrize(
    "stored_value",
    [None, 42, {"candidate": "not-a-list"}],
)
def test_backfill_preview_rejects_non_list_candidate_storage(
    db_session,
    seed_users,
    stored_value,
):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="malformed-preview-container",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    snapshot.preview.candidates_json = stored_value
    db_session.add(snapshot.preview)
    db_session.commit()

    with pytest.raises(AlertBackfillPreviewError) as error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )

    assert error.value.code == "alert_backfill_preview_invalid"
    assert "candidate list" in str(error.value)


@pytest.mark.parametrize(
    "corruption",
    ["reordered", "nonhex", "incomplete", "inverted_window", "cursor_outside"],
)
def test_backfill_preview_rejects_inconsistent_candidate_pages(
    db_session,
    seed_users,
    corruption,
):
    _feed_one, item_one, _rule_one, _classification_one = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix=f"preview-page-{corruption}-one",
    )
    _feed_two, item_two, _rule_two, _classification_two = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix=f"preview-page-{corruption}-two",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=min(item_one.first_seen_at, item_two.first_seen_at)
        - timedelta(seconds=1),
        until=max(item_one.first_seen_at, item_two.first_seen_at)
        + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    assert len(snapshot.preview.candidates_json) == 2
    entries = copy.deepcopy(snapshot.preview.candidates_json)
    if corruption == "reordered":
        entries.reverse()
    elif corruption == "nonhex":
        entries[0]["content_hash"] = "Z" * 64
    elif corruption == "incomplete":
        snapshot.preview.matched_count += 1
    elif corruption == "inverted_window":
        snapshot.preview.since = snapshot.preview.until + timedelta(seconds=1)
    else:
        snapshot.preview.cursor_first_seen_at = snapshot.preview.until + timedelta(
            seconds=1
        )
        snapshot.preview.cursor_item_id = uuid.uuid4()
    snapshot.preview.candidates_json = entries
    db_session.add(snapshot.preview)
    db_session.commit()

    with pytest.raises(AlertBackfillPreviewError) as error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )

    assert error.value.code == "alert_backfill_preview_invalid"


def test_backfill_replay_validates_request_binding_and_legacy_receipts(
    db_session,
    seed_users,
):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="bound-apply-receipt",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    applied = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    db_session.commit()
    assert len(applied.request_ids) == 1
    valid_entries = copy.deepcopy(snapshot.preview.candidates_json)

    unrelated_id = uuid.uuid4()
    corrupted_entries = copy.deepcopy(valid_entries)
    corrupted_envelope = corrupted_entries[-1]["_threatlens_apply_result"]
    corrupted_envelope["outcomes"][0]["request_id"] = str(unrelated_id)
    snapshot.preview.candidates_json = corrupted_entries
    db_session.add(snapshot.preview)
    db_session.commit()
    with pytest.raises(AlertBackfillPreviewError) as invalid_error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
    assert invalid_error.value.code == "alert_backfill_apply_result_invalid"
    db_session.rollback()

    unsupported_entries = copy.deepcopy(valid_entries)
    unsupported_entries[-1]["_threatlens_apply_result"]["version"] = 99
    snapshot.preview.candidates_json = unsupported_entries
    db_session.add(snapshot.preview)
    db_session.commit()
    with pytest.raises(AlertBackfillPreviewError) as unsupported_error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
    assert unsupported_error.value.code == "alert_backfill_apply_result_unsupported"
    db_session.rollback()

    candidate = valid_entries[0]
    legacy_fingerprint = hashlib.sha256(
        json.dumps(
            [
                {
                    "item_id": candidate["item_id"],
                    "content_hash": candidate["content_hash"],
                }
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    legacy_entries = [
        candidate,
        {
            "_threatlens_apply_result": {
                "version": 2,
                "candidate_fingerprint": legacy_fingerprint,
                "request_ids": [str(applied.request_ids[0])],
                "requests": [
                    {
                        "request_id": str(applied.request_ids[0]),
                        "item_id": candidate["item_id"],
                        "content_hash": candidate["content_hash"],
                        "notify": False,
                    }
                ],
                "existing_count": 0,
                "skipped_count": 0,
                "dispatch_state": "published",
            }
        },
    ]
    snapshot.preview.candidates_json = legacy_entries
    db_session.add(snapshot.preview)
    db_session.commit()

    for dispatch_state, enqueue_failed in (
        ("published", False),
        ("pending", True),
        ("deferred", True),
    ):
        state_entries = copy.deepcopy(legacy_entries)
        state_entries[-1]["_threatlens_apply_result"]["dispatch_state"] = dispatch_state
        snapshot.preview.candidates_json = state_entries
        db_session.add(snapshot.preview)
        db_session.commit()
        replayed = persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
        assert replayed.replayed is True
        assert replayed.request_ids == applied.request_ids
        assert replayed.enqueue_failed is enqueue_failed

    legacy_entries = copy.deepcopy(legacy_entries)
    legacy_entries[-1]["_threatlens_apply_result"] = {
        "version": 1,
        "request_ids": [str(applied.request_ids[0])],
        "existing_count": 0,
        "skipped_count": 0,
    }
    snapshot.preview.candidates_json = legacy_entries
    db_session.add(snapshot.preview)
    db_session.commit()
    replayed_v1 = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    assert replayed_v1.request_ids == applied.request_ids
    assert replayed_v1.enqueue_failed is False


def test_v2_backfill_receipt_rejects_swapped_request_candidate_bindings(
    db_session,
    seed_users,
):
    _feed_one, item_one, _rule_one, _classification_one = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="v2-binding-one",
    )
    _feed_two, item_two, _rule_two, _classification_two = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="v2-binding-two",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=min(item_one.first_seen_at, item_two.first_seen_at)
        - timedelta(seconds=1),
        until=max(item_one.first_seen_at, item_two.first_seen_at)
        + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    applied = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    db_session.commit()
    candidates = copy.deepcopy(snapshot.preview.candidates_json[:-1])
    requests = {
        row.id: row
        for row in db_session.scalars(
            select(AlertEvaluationRequest).where(
                AlertEvaluationRequest.id.in_(applied.request_ids)
            )
        ).all()
    }
    assert len(candidates) == len(requests) == 2
    candidate_by_item = {
        uuid.UUID(candidate["item_id"]): candidate for candidate in candidates
    }
    request_rows = list(requests.values())
    swapped_candidates = [
        candidate_by_item[request_rows[1].item_id],
        candidate_by_item[request_rows[0].item_id],
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                {
                    "item_id": candidate["item_id"],
                    "content_hash": candidate["content_hash"],
                }
                for candidate in candidates
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snapshot.preview.candidates_json = [
        *candidates,
        {
            "_threatlens_apply_result": {
                "version": 2,
                "candidate_fingerprint": fingerprint,
                "request_ids": [str(row.id) for row in request_rows],
                "requests": [
                    {
                        "request_id": str(row.id),
                        "item_id": candidate["item_id"],
                        "content_hash": candidate["content_hash"],
                        "notify": False,
                    }
                    for row, candidate in zip(
                        request_rows, swapped_candidates, strict=True
                    )
                ],
                "existing_count": 0,
                "skipped_count": 0,
                "dispatch_state": "published",
            }
        },
    ]
    db_session.add(snapshot.preview)
    db_session.commit()

    with pytest.raises(AlertBackfillPreviewError) as error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
    assert error.value.code == "alert_backfill_apply_result_invalid"


def test_v3_backfill_receipt_is_bound_to_its_exact_acceptance_activity(
    db_session,
    seed_users,
):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="v3-activity-binding",
    )
    now = datetime.now(timezone.utc)
    snapshot = create_alert_backfill_preview(
        db_session,
        actor_user_id=seed_users["admin"].id,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        now=now,
    )
    applied = persist_alert_backfill_preview_intents(
        db_session,
        preview_id=snapshot.preview.id,
        actor_user_id=seed_users["admin"].id,
        now=now,
    )
    db_session.commit()
    entries = copy.deepcopy(snapshot.preview.candidates_json)
    outcome = entries[-1]["_threatlens_apply_result"]["outcomes"][0]
    original = db_session.get(
        AlertEvaluationRequestActivity, uuid.UUID(outcome["activity_id"])
    )
    assert original is not None
    forged = AlertEvaluationRequestActivity(
        request_id=applied.request_ids[0],
        actor_user_id=seed_users["admin"].id,
        action=original.action,
        details_json={
            **original.details_json,
            "backfill_preview_id": str(uuid.uuid4()),
        },
    )
    db_session.add(forged)
    db_session.flush()
    outcome["activity_id"] = str(forged.id)
    snapshot.preview.candidates_json = entries
    db_session.add(snapshot.preview)
    db_session.commit()

    with pytest.raises(AlertBackfillPreviewError) as error:
        persist_alert_backfill_preview_intents(
            db_session,
            preview_id=snapshot.preview.id,
            actor_user_id=seed_users["admin"].id,
            now=now,
        )
    assert error.value.code == "alert_backfill_apply_result_invalid"


def test_simultaneous_backfill_applies_share_one_durable_result(database_engine):
    actor_id = uuid.uuid4()
    suffix = f"concurrent-apply-{uuid.uuid4().hex}"
    with Session(database_engine) as setup_db:
        actor = User(
            id=actor_id,
            email=f"{suffix}@example.com",
            password_hash="test-hash",
            role="admin",
            is_active=True,
            is_approved=True,
        )
        setup_db.add(actor)
        setup_db.commit()
        feed, item, _rule, _classification = _seed_target(
            setup_db,
            actor,
            suffix=suffix,
        )
        now = datetime.now(timezone.utc)
        snapshot = create_alert_backfill_preview(
            setup_db,
            actor_user_id=actor_id,
            since=item.first_seen_at - timedelta(seconds=1),
            until=item.first_seen_at + timedelta(seconds=1),
            limit=10,
            now=now,
        )
        setup_db.commit()
        preview_id = snapshot.preview.id
        item_id = item.id
        feed_id = feed.id

    start = Barrier(2)

    def _apply() -> tuple[bool, tuple[uuid.UUID, ...]]:
        with Session(database_engine) as worker_db:
            start.wait(timeout=5)
            result = persist_alert_backfill_preview_intents(
                worker_db,
                preview_id=preview_id,
                actor_user_id=actor_id,
                now=now,
            )
            worker_db.commit()
            return result.replayed, result.request_ids

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=10)
                for future in [
                    executor.submit(_apply),
                    executor.submit(_apply),
                ]
            ]

        assert sorted(replayed for replayed, _request_ids in results) == [False, True]
        assert results[0][1] == results[1][1]
        assert len(results[0][1]) == 1
        with Session(database_engine) as verify_db:
            requests = list(
                verify_db.scalars(
                    select(AlertEvaluationRequest).where(
                        AlertEvaluationRequest.item_id == item_id
                    )
                ).all()
            )
            assert len(requests) == 1
            assert requests[0].dispatch_claimed_at is None
            assert requests[0].dispatch_attempt_count == 0
    finally:
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AlertEvaluationRequest).where(
                    AlertEvaluationRequest.item_id == item_id
                )
            )
            cleanup_db.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup_db.execute(delete(User).where(User.id == actor_id))
            cleanup_db.commit()


def test_backfill_reset_preserves_live_provenance_and_never_notifies(
    db_session,
    seed_users,
):
    _feed, item, rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="backfill-provenance",
    )
    intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    _evaluate(db_session, intent.request_id)
    rule.revision = 2
    rule.name = "Backfill revision two"
    db_session.add(rule)
    db_session.commit()

    result = persist_alert_backfill_intents(
        db_session,
        since=item.first_seen_at - timedelta(seconds=1),
        until=item.first_seen_at + timedelta(seconds=1),
        limit=10,
        actor_user_id=seed_users["admin"].id,
    )
    db_session.commit()
    assert result.request_ids == (intent.request_id,)
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.source == "live"
    assert request.active_source == "backfill"
    assert request.notify is False
    assert request.backfill_count == 1
    assert request.last_backfill_at is not None

    outcome = _evaluate(db_session, intent.request_id)
    assert outcome.occurrences_created == 1
    assert outcome.integration_event_ids == ()
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 1

    promoted = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    assert promoted.request_id == intent.request_id
    assert promoted.created is True
    request = db_session.get(AlertEvaluationRequest, intent.request_id)
    assert request.source == "live"
    assert request.active_source == "live"
    assert request.notify is True
    promoted_outcome = _evaluate(db_session, promoted.request_id)
    assert promoted_outcome.occurrences_created == 0
    assert promoted_outcome.evaluated_rules == 0
    assert promoted_outcome.integration_event_ids == ()
    assert db_session.scalar(select(func.count(IntegrationEvent.id))) == 1
    actions = set(
        db_session.scalars(
            select(AlertEvaluationRequestActivity.action).where(
                AlertEvaluationRequestActivity.request_id == intent.request_id
            )
        ).all()
    )
    assert {
        "accepted",
        "backfill_requested",
        "promoted_to_live",
        "succeeded",
    } <= actions


def test_retention_delete_rechecks_terminal_state_and_cutoff(db_session, seed_users):
    _feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="retention-reset-race",
    )
    old = datetime.now(timezone.utc) - timedelta(days=60)
    request = AlertEvaluationRequest(
        item_id=item.id,
        item_content_hash=item.content_hash,
        state="succeeded",
        completed_at=old,
        created_at=old,
        updated_at=old,
    )
    db_session.add(request)
    db_session.commit()
    request_id = request.id

    request.state = "pending"
    request.completed_at = None
    db_session.add(request)
    db_session.flush()
    deleted = _delete_terminal_evaluation_ids(
        db_session,
        [request_id],
        cutoff=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db_session.commit()
    assert deleted == 0
    assert db_session.get(AlertEvaluationRequest, request_id) is not None


def test_multi_owner_smtp_and_webhook_projection_uses_owner_counts_and_bounds(
    db_session,
    seed_users,
):
    feed, item, _rule, _classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="smtp-owner-projection",
    )
    first_owner = seed_users["viewer"].id
    second_owner = seed_users["analyst"].id
    first_occurrences = [uuid.uuid4() for _ in range(500)]
    second_occurrences = [uuid.uuid4(), uuid.uuid4()]
    contexts = {
        first_owner: AlertMatchContext(
            count=600,
            primary_name="Viewer-only rule",
            names=["Viewer-only rule"],
            categories=["viewer"],
            matched_keywords=["fortinet"],
        ),
        second_owner: AlertMatchContext(
            count=2,
            primary_name="Analyst-only rule",
            names=["Analyst-only rule"],
            categories=["analyst"],
            matched_keywords=["exploitation"],
        ),
    }
    payload = build_alert_match_snapshot_payload(
        item=item,
        feed=feed,
        contexts_by_owner=contexts,
        occurrence_ids=[*first_occurrences, *second_occurrences],
        occurrence_count=602,
        occurrence_ids_truncated=True,
        evaluation_request_id=uuid.uuid4(),
    )
    payload["occurrence_ids_by_owner"] = [
        {
            "owner_user_id": str(first_owner),
            "occurrence_ids": [
                str(occurrence_id) for occurrence_id in first_occurrences
            ],
        },
        {
            "owner_user_id": str(second_owner),
            "occurrence_ids": [
                str(occurrence_id) for occurrence_id in second_occurrences
            ],
        },
    ]
    event = IntegrationEvent(
        id=uuid.uuid4(),
        event_type="alert_match",
        schema_version=2,
        source_type="item",
        source_id=str(item.id),
        idempotency_key=f"smtp-owner-projection:{item.id}",
        payload_json=payload,
    )
    instances = []
    for owner_id, label in ((first_owner, "viewer"), (second_owner, "analyst")):
        instance = IntegrationInstance(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            name=f"SMTP {label}",
            integration_type="smtp",
            direction="destination",
            enabled=True,
            config_json={
                "host": "smtp.example.com",
                "from_email": "threatlens@example.com",
                "to_emails": [f"{label}@example.com"],
                "event_types": ["alert_match"],
                "feed_scope": "all",
            },
        )
        instances.append(instance)
    db_session.add_all([event, *instances])
    db_session.flush()

    connector = SMTPIntegrationConnector()
    connector.prepare_routing(db_session, event=event)
    routed = connector.route_event(db_session, event=event)
    deliveries = list(
        db_session.scalars(
            select(IntegrationDelivery).where(
                IntegrationDelivery.id.in_(routed.delivery_ids)
            )
        ).all()
    )
    assert len(deliveries) == 2
    by_owner = {
        delivery.owner_user_id: delivery.payload_json for delivery in deliveries
    }
    assert by_owner[first_owner]["alert"]["names"] == ["Viewer-only rule"]
    assert by_owner[first_owner]["occurrence_ids"] == [
        str(occurrence_id) for occurrence_id in first_occurrences
    ]
    assert by_owner[first_owner]["occurrence_count"] == 600
    assert by_owner[first_owner]["occurrence_ids_truncated"] is True
    assert by_owner[second_owner]["alert"]["names"] == ["Analyst-only rule"]
    assert by_owner[second_owner]["occurrence_ids"] == [
        str(occurrence_id) for occurrence_id in second_occurrences
    ]
    assert by_owner[second_owner]["occurrence_count"] == 2
    assert by_owner[second_owner]["occurrence_ids_truncated"] is False
    assert all("alert_matches" not in projected for projected in by_owner.values())
    assert all(
        "occurrence_ids_by_owner" not in projected for projected in by_owner.values()
    )

    webhooks = [
        NotificationWebhook(
            id=uuid.uuid4(),
            user_id=owner_id,
            name=f"Webhook {label}",
            enabled=True,
            event_type="alert_match",
            url_template=f"https://example.com/{label}-alerts",
            method="POST",
            feed_scope="all",
            feed_ids_json=[],
            query_params_json=[],
            headers_json=[],
            body_mode="none",
            body_fields_json=[],
            timeout_seconds=10,
        )
        for owner_id, label in ((first_owner, "viewer"), (second_owner, "analyst"))
    ]
    db_session.add_all(webhooks)
    db_session.flush()
    webhook_connector = WebhookIntegrationConnector()
    webhook_connector.prepare_routing(db_session, event=event)
    webhook_routing = webhook_connector.route_event(db_session, event=event)
    webhook_deliveries = list(
        db_session.scalars(
            select(IntegrationDelivery).where(
                IntegrationDelivery.id.in_(webhook_routing.delivery_ids)
            )
        ).all()
    )
    webhook_by_owner = {
        delivery.owner_user_id: delivery.payload_json for delivery in webhook_deliveries
    }
    assert set(webhook_by_owner) == {first_owner, second_owner}
    assert webhook_by_owner[first_owner]["occurrence_count"] == 600
    assert webhook_by_owner[first_owner]["occurrence_ids_truncated"] is True
    assert webhook_by_owner[second_owner]["occurrence_count"] == 2
    assert webhook_by_owner[second_owner]["occurrence_ids_truncated"] is False

    no_owner_map = dict(payload)
    no_owner_map.pop("occurrence_ids_by_owner")
    event_without_map = IntegrationEvent(
        event_type="alert_match",
        schema_version=2,
        source_type="item",
        idempotency_key=f"smtp-owner-projection-no-map:{item.id}",
        payload_json=no_owner_map,
    )
    projected = delivery_payload_for_owner(
        event_without_map,
        owner_user_id=first_owner,
    )
    assert projected["occurrence_ids"] == []
    assert projected["occurrence_count"] == 600
    assert projected["occurrence_ids_truncated"] is True

    malformed = IntegrationEvent(
        event_type="alert_match",
        schema_version=3,
        source_type="item",
        idempotency_key=f"smtp-owner-projection-malformed:{item.id}",
        payload_json={
            **payload,
            "schema_version": 3,
            "owner_user_id": str(first_owner),
        },
    )
    with pytest.raises(IntegrationEventContextError, match="inconsistent owner"):
        alert_match_event_owner_ids(malformed)


def test_snapshot_webhook_reservation_is_idempotent_per_event_not_per_item(
    db_session,
    seed_users,
):
    feed, item, _rule, classification = _seed_target(
        db_session,
        seed_users["viewer"],
        suffix="snapshot-webhook-event-scope",
    )
    webhook = NotificationWebhook(
        id=uuid.uuid4(),
        user_id=seed_users["viewer"].id,
        name="Snapshot alert webhook",
        enabled=True,
        event_type="alert_match",
        url_template="https://example.com/snapshot-alerts",
        method="POST",
        feed_scope="all",
        feed_ids_json=[],
        query_params_json=[],
        headers_json=[],
        body_mode="none",
        body_fields_json=[],
        timeout_seconds=10,
    )
    db_session.add(webhook)
    db_session.commit()

    first_intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    first_outcome = _evaluate(db_session, first_intent.request_id)
    assert len(first_outcome.integration_event_ids) == 1
    first_event = db_session.get(
        IntegrationEvent, first_outcome.integration_event_ids[0]
    )
    assert first_event is not None

    connector = WebhookIntegrationConnector()
    connector.prepare_routing(db_session, event=first_event)
    first_routing = connector.route_event(db_session, event=first_event)
    db_session.commit()
    assert len(first_routing.compatibility_delivery_ids) == 1

    first_compatibility = db_session.get(
        NotificationWebhookDelivery,
        first_routing.compatibility_delivery_ids[0],
    )
    assert first_compatibility is not None
    assert first_compatibility.scope_key == f"alert_event:{first_event.id}"

    # Simulate a row reserved by the pre-scope v2 connector. Its generic delivery
    # already links it to this event, so rerouting must adopt rather than duplicate it.
    first_compatibility.scope_key = None
    db_session.add(first_compatibility)
    db_session.commit()
    legacy_reroute = connector.route_event(db_session, event=first_event)
    db_session.commit()
    assert legacy_reroute.compatibility_delivery_ids == ()
    db_session.refresh(first_compatibility)
    assert first_compatibility.scope_key == f"alert_event:{first_event.id}"

    item.summary = "Researchers observed changed exploitation details."
    item.content_hash = hashlib.sha256(b"snapshot-webhook-event-scope-v2").hexdigest()
    db_session.add(item)
    db_session.commit()
    second_intent = persist_alert_evaluation_intent(
        db_session,
        item=item,
        classification=classification,
    )
    db_session.commit()
    second_outcome = _evaluate(db_session, second_intent.request_id)
    assert len(second_outcome.integration_event_ids) == 1
    second_event = db_session.get(
        IntegrationEvent,
        second_outcome.integration_event_ids[0],
    )
    assert second_event is not None
    assert second_event.id != first_event.id

    second_routing = connector.route_event(db_session, event=second_event)
    db_session.commit()
    assert len(second_routing.compatibility_delivery_ids) == 1
    repeated_routing = connector.route_event(db_session, event=second_event)
    db_session.commit()
    assert repeated_routing.compatibility_delivery_ids == ()

    compatibility_deliveries = list(
        db_session.scalars(
            select(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.webhook_id == webhook.id,
                NotificationWebhookDelivery.event_type_snapshot == "alert_match",
                NotificationWebhookDelivery.delivery_kind == "live",
            )
            .order_by(NotificationWebhookDelivery.attempted_at.asc())
        ).all()
    )
    assert len(compatibility_deliveries) == 2
    assert {delivery.scope_key for delivery in compatibility_deliveries} == {
        f"alert_event:{first_event.id}",
        f"alert_event:{second_event.id}",
    }
    assert all(
        delivery.integration_delivery_id is not None
        for delivery in compatibility_deliveries
    )
    assert set(
        db_session.scalars(
            select(IntegrationDelivery.event_id).where(
                IntegrationDelivery.id.in_(
                    [
                        delivery.integration_delivery_id
                        for delivery in compatibility_deliveries
                    ]
                )
            )
        ).all()
    ) == {first_event.id, second_event.id}


def test_alert_v2_models_are_registered_in_base_metadata():
    exported_models = {
        "AlertEvaluationMatch",
        "AlertEvaluationRequest",
        "AlertEvaluationRequestActivity",
        "AlertOccurrence",
        "AlertOccurrenceActivity",
        "AlertOccurrenceMetric",
    }
    assert exported_models <= set(app.models.__all__)
    assert all(hasattr(app.models, name) for name in exported_models)
    assert {
        "alert_evaluation_requests",
        "alert_evaluation_matches",
        "alert_evaluation_request_activities",
        "alert_occurrences",
        "alert_occurrence_activities",
        "alert_occurrence_metrics",
    } <= set(Base.metadata.tables)
