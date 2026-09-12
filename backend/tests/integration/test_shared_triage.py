"""Team ownership remains durable across evaluation, deletion and retention."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceMetric
from app.models.feed import Feed
from app.models.iam import IAMGroup
from app.models.item import Item
from app.models.team import Team
from app.models.user import User
from app.services.alert_evaluation import (
    claim_alert_evaluation_request,
    evaluate_alert_request,
    persist_alert_evaluation_intent,
)
from app.services.alert_maintenance import maintain_alert_history


def test_team_evaluation_preserves_sla_and_ownership_through_retention(
    db_session, seed_users
):
    now = datetime.now(timezone.utc)
    group = IAMGroup(key="triage-members", name="Triage members")
    db_session.add(group)
    db_session.flush()
    team = Team(
        key="triage",
        name="Triage",
        membership_group_id=group.id,
        created_by_user_id=seed_users["viewer"].id,
    )
    feed = Feed(name="Triage feed", url="https://example.com/triage.xml")
    db_session.add_all([team, feed])
    db_session.flush()
    rule = AlertInterest(
        team_id=team.id,
        name="Team watch",
        category="appliance",
        keywords=["fortinet"],
        durable_since=now - timedelta(days=1),
        due_after_minutes=60,
        escalation_after_minutes=30,
    )
    item = Item(
        feed_id=feed.id,
        source_guid="triage",
        url="https://example.com/triage",
        canonical_url="https://example.com/triage",
        title="Fortinet exploitation",
        summary="Current evidence",
        first_seen_at=now,
        dedupe_key="triage",
        content_hash="a" * 64,
        status="content_fetched",
    )
    db_session.add_all([rule, item])
    db_session.flush()
    intent = persist_alert_evaluation_intent(db_session, item=item)
    db_session.commit()
    # Accepted work retains its SLA even when the mutable rule is edited.
    rule.due_after_minutes = 120
    db_session.commit()
    claim = claim_alert_evaluation_request(db_session, request_id=intent.request_id)
    db_session.commit()
    outcome = evaluate_alert_request(
        db_session, request_id=intent.request_id, lease_token=claim.lease_token, now=now
    )
    db_session.commit()
    assert outcome.occurrences_created == 1
    assert not outcome.integration_event_ids
    occurrence = db_session.scalar(
        select(AlertOccurrence).where(AlertOccurrence.team_id == team.id)
    )
    assert occurrence.owner_user_id is None
    assert occurrence.due_at == now + timedelta(minutes=60)
    assert occurrence.escalation_after_minutes == 30
    occurrence_id = occurrence.id
    db_session.execute(delete(User).where(User.id == seed_users["viewer"].id))
    db_session.commit()
    assert db_session.get(AlertOccurrence, occurrence_id) is not None
    occurrence.lifecycle_state = "closed"
    occurrence.closure_disposition = "true_positive"
    occurrence.closed_at = now - timedelta(days=10)
    occurrence.created_at = now - timedelta(days=10)
    db_session.commit()
    result = maintain_alert_history(db_session, now=now, occurrence_retention_days=1)
    assert result.occurrences_aggregated == 1
    assert result.occurrences_deleted == 1
    metric = db_session.scalar(
        select(AlertOccurrenceMetric).where(AlertOccurrenceMetric.team_id == team.id)
    )
    assert metric.owner_user_id is None
    assert metric.occurrence_count == 1
