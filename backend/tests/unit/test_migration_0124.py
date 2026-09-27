"""Successful progress is distinct from old attempt timestamps after upgrade."""

from datetime import datetime, timedelta, timezone
import uuid

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.db.base import Base
from app.models.publication_consumer import PublicationConsumer
from tests.unit.test_migration_0107 import _migration
from tests.unit.test_migration_0115 import owner_team


def test_reconciliation_upgrade_does_not_invent_historical_success(db_session, monkeypatch):
    owner, team = owner_team(db_session)
    old_attempt = datetime.now(timezone.utc) - timedelta(hours=1)
    row = PublicationConsumer(team_id=team.id, principal_id=owner.id, name='Migration consumer',
        idempotency_key=uuid.uuid4(), request_digest='b' * 64, authorization_encrypted={}, token_hash='a' * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1), last_reconciled_at=old_attempt)
    db_session.add(row)
    db_session.flush()
    identifier = row.id
    migration = _migration(db_session, monkeypatch, '0124_reconciliation_progress')
    migration.downgrade()
    assert db_session.scalar(text('SELECT last_reconciled_at FROM publication_consumers WHERE id=:id'), {'id': identifier}) == old_attempt
    migration.upgrade()
    db_session.expire(row)
    assert row.last_reconcile_attempt_at == old_attempt
    assert row.last_reconciled_at is None and row.reconciliation_error_code is None
    names = {'publication_consumers', 'automation_executions'}
    context = MigrationContext.configure(db_session.connection(), opts={'include_object':
        lambda obj, name, kind, reflected, compared: name in names if kind == 'table'
        else getattr(getattr(obj, 'table', None), 'name', None) in names})
    assert compare_metadata(context, Base.metadata) == []
