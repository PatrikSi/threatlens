"""An attempted or partial sweep cannot make overdue publication work healthy."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.publication_consumer import PublicationConsumer, PublicationSubscription
from app.services.automation_health import automation_backlog
from app.services.publication_consumers import reconcile_consumer
from app.tasks import publication_distribution_tasks as tasks
from tests.integration.test_indicator_publications import reviewed as reviewed_fixture, publish
from tests.integration.test_indicator_intelligence import intel_setup  # noqa: F401
from tests.integration.test_publication_distribution import register

reviewed = reviewed_fixture


def _subscribed(client, reviewed, auth_headers, db):
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, headers = register(client, reviewed, auth_headers)
    result = client.post(f"{path}/{consumer['id']}/subscriptions",
        json={"publication_id": publication['id']}, headers=auth_headers['admin'])
    assert result.status_code == 204, result.text
    row = db.get(PublicationConsumer, uuid.UUID(consumer['id']))
    row.last_reconciled_at = datetime.now(timezone.utc) - timedelta(hours=2)
    row.last_reconcile_attempt_at = None
    db.commit()
    return row, headers


@pytest.mark.parametrize("failure_stage", ["reconcile_consumer", "prune_acknowledged_changes"])
def test_failed_sweep_stays_degraded_and_successful_retry_recovers(
    client, reviewed, auth_headers, db_session, monkeypatch, failure_stage,
):
    row, _ = _subscribed(client, reviewed, auth_headers, db_session)
    previous_success = row.last_reconciled_at
    monkeypatch.setattr(tasks, 'SessionLocal', sessionmaker(
        bind=db_session.bind, autoflush=False, join_transaction_mode='create_savepoint'))

    def fail(_db, _consumer):
        raise RuntimeError('Synthetic reconciliation failure')

    original = getattr(tasks, failure_stage)
    monkeypatch.setattr(tasks, failure_stage, fail)
    assert tasks.reconcile_distribution.run() == {'subscriptions_checked': 0}
    db_session.expire_all()
    assert row.last_reconciled_at == previous_success
    assert row.last_reconcile_attempt_at > previous_success
    assert row.reconciliation_error_code == 'publication_reconciliation_failed'
    status = automation_backlog(db_session, key='publication_reconciliation', now=datetime.now(timezone.utc))
    assert status.status == 'degraded' and status.pending_count == 1
    # Retry admission uses the attempted time, without falsifying successful work.
    assert tasks.reconcile_distribution.run() == {'subscriptions_checked': 0}
    row.last_reconcile_attempt_at -= timedelta(minutes=2)
    db_session.commit()
    monkeypatch.setattr(tasks, failure_stage, original)
    tasks.reconcile_distribution.run()
    db_session.expire_all()
    assert row.last_reconciled_at > previous_success
    assert row.reconciliation_error_at is None and row.reconciliation_error_code is None
    assert automation_backlog(db_session, key='publication_reconciliation', now=datetime.now(timezone.utc)).status == 'healthy'


def test_partial_success_does_not_hide_overdue_subscriptions(client, reviewed, auth_headers, db_session):
    row, _ = _subscribed(client, reviewed, auth_headers, db_session)
    _, _, publication = publish(client, reviewed, auth_headers)
    response = client.post(f"/teams/{row.team_id}/publication-consumers/{row.id}/subscriptions",
        json={"publication_id": publication['id']}, headers=auth_headers['admin'])
    assert response.status_code == 204, response.text
    subscriptions = db_session.scalars(select(PublicationSubscription).where(PublicationSubscription.consumer_id == row.id)).all()
    assert len(subscriptions) == 2
    for subscription in subscriptions:
        subscription.next_check_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.flush()
    assert reconcile_consumer(db_session, row, limit=1) == 1
    db_session.flush()
    assert automation_backlog(db_session, key='publication_reconciliation', now=datetime.now(timezone.utc)).status == 'degraded'
    reconcile_consumer(db_session, row)
    assert automation_backlog(db_session, key='publication_reconciliation', now=datetime.now(timezone.utc)).status == 'healthy'


def test_late_failure_does_not_replace_newer_success(client, reviewed, auth_headers, db_session, monkeypatch):
    row, _ = _subscribed(client, reviewed, auth_headers, db_session)
    attempted = datetime.now(timezone.utc) - timedelta(seconds=10)
    row.last_reconcile_attempt_at = attempted
    row.last_reconciled_at = datetime.now(timezone.utc)
    db_session.commit()
    monkeypatch.setattr(tasks, 'SessionLocal', sessionmaker(
        bind=db_session.bind, autoflush=False, join_transaction_mode='create_savepoint'))
    tasks._record_failure(row.id, attempted)
    db_session.expire_all()
    assert row.reconciliation_error_at is None and row.reconciliation_error_code is None
