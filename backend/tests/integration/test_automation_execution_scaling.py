"""Keep receiver worklist reads and fenced reconciliation query growth bounded."""

import uuid

import pytest
from sqlalchemy import event, select

from app.models.article import Article
from app.models.automation_execution import AutomationExecution
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.notification_webhook import NotificationWebhook
from app.services.automation_executions import reconcile_executions, register_execution
from app.services.intel_events import emit_intel_events
from tests.integration.test_automation_executions import execution as execution_fixture

execution = execution_fixture


@pytest.mark.parametrize("size", [25, 100])
def test_list_and_reconcile_query_counts(
    client, auth_headers, execution, db_session, size
):
    original_event = db_session.get(IntegrationEvent, execution.event_id)
    original_item = db_session.get(Item, original_event.source_id)
    webhook = db_session.get(NotificationWebhook, execution.webhook_id)
    for _index in range(size - 1):
        item = Item(
            feed_id=original_item.feed_id,
            title="Bounded receiver source",
            url="https://source.example/scale",
            dedupe_key=str(uuid.uuid4()),
            content_hash="a" * 64,
        )
        db_session.add(item)
        db_session.flush()
        db_session.add(
            Article(
                item_id=item.id,
                text="Bounded intelligence evidence",
                final_url=item.url,
                http_status=200,
            )
        )
        db_session.flush()
        event_id = emit_intel_events(db_session, item_id=item.id, deterministic=True)[0]
        register_execution(
            db_session,
            event=db_session.get(IntegrationEvent, event_id),
            webhook=webhook,
        )
    db_session.commit()
    statements = []
    connection = db_session.connection()

    def count(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", count)
    try:
        response = client.get(
            "/notifications/automation/executions",
            headers=auth_headers["analyst"],
            params={"limit": size},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == size
        list_queries = len(statements)
        statements.clear()
        assert reconcile_executions(db_session, limit=size) == size
        db_session.flush()
        reconcile_queries = len(statements)
    finally:
        event.remove(connection, "before_cursor_execute", count)
    print(
        f"AUTOMATION_SQL size={size} list={list_queries} reconcile={reconcile_queries}"
    )
    assert (
        db_session.scalars(select(AutomationExecution.policy_state)).all()
        == ["current"] * size
    )


@pytest.mark.parametrize("origin", ["direct", "group"])
def test_batch_rechecks_expiring_oidc_access(db_session, seed_users, execution, origin):
    from datetime import datetime, timedelta, timezone
    from app.models.iam import (
        IAMGroupMembership,
        IAMGroupRoleAssignment,
        IAMRolePermission,
        IAMUserRoleAssignment,
    )
    from app.services.automation_execution_authority import ExecutionAuthorityBatch
    from tests.integration.test_oidc_access_sync import _sync_fixture

    setup = _sync_fixture(db_session, seed_users)
    execution.owner_user_id = seed_users["viewer"].id
    db_session.add(
        IAMRolePermission(
            role_id=setup["mapped_role"].id, permission="write:notifications"
        )
    )
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    if origin == "direct":
        mapping = setup["role_mapping"]
        assignment = IAMUserRoleAssignment(
            user_id=execution.owner_user_id,
            role_id=mapping.role_id,
            source="oidc",
            source_key=mapping.source_key,
            oidc_role_mapping_id=mapping.id,
            oidc_assertion_expires_at=expires,
        )
    else:
        mapping = setup["group_mapping"]
        db_session.add(
            IAMGroupRoleAssignment(
                group_id=mapping.group_id, role_id=setup["mapped_role"].id
            )
        )
        assignment = IAMGroupMembership(
            user_id=execution.owner_user_id,
            group_id=mapping.group_id,
            source="oidc",
            source_key=mapping.source_key,
            oidc_group_mapping_id=mapping.id,
            oidc_assertion_expires_at=expires,
        )
    db_session.add(assignment)
    db_session.flush()
    batch = ExecutionAuthorityBatch(db_session, [execution])
    stored = db_session.get(IntegrationEvent, execution.event_id)
    assert batch.eligible(owner_user_id=execution.owner_user_id, event=stored)
    assignment.oidc_assertion_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db_session.flush()
    assert not batch.eligible(owner_user_id=execution.owner_user_id, event=stored)


def test_batch_keeps_each_owners_handling_boundary(
    db_session, seed_users, execution, monkeypatch
):
    from app.models.data_policy import DataPolicyState
    from app.services.automation_execution_authority import ExecutionAuthorityBatch
    from app.services.data_access_envelopes import (
        DataAccessSourceInput,
        replace_data_access_envelope_sources,
    )
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement

    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    stored = db_session.get(IntegrationEvent, execution.event_id)
    replace_data_access_envelope_sources(
        db_session,
        resource_type="integration_event",
        resource_id=stored.id,
        sources=[
            DataAccessSourceInput(
                source_type="item",
                source_id=str(stored.source_id),
                source_version="1",
                handling_label_id=restricted.id,
                captured_policy_revision=db_session.get(DataPolicyState, 1).revision,
            )
        ],
    )
    other = AutomationExecution(
        owner_user_id=seed_users["admin"].id,
        webhook_id=uuid.uuid4(),
        event_id=execution.event_id,
        action_id="same-restricted-evidence",
    )
    db_session.add(other)
    db_session.flush()
    batch = ExecutionAuthorityBatch(db_session, [execution, other])
    assert batch.eligible(owner_user_id=other.owner_user_id, event=stored)
    assert not batch.eligible(owner_user_id=execution.owner_user_id, event=stored)
    assert batch.eligible(owner_user_id=other.owner_user_id, event=stored)


def test_batch_rechecks_team_activation_and_does_not_cache_unavailable_policy(
    client, db_session, seed_users, auth_headers, execution, monkeypatch
):
    from app.models.team import Team
    from app.services import automation_execution_authority as authority
    from app.services.intel_event_eligibility import IntelEventBusy
    from tests.integration.test_teams_api import _team

    team, _, _ = _team(client, db_session, seed_users, auth_headers)
    execution.owner_user_id = seed_users["admin"].id
    stored = db_session.get(IntegrationEvent, execution.event_id)
    stored.payload_json = {**stored.payload_json, "team_id": team["id"]}
    db_session.flush()
    batch = authority.ExecutionAuthorityBatch(db_session, [execution])
    original = authority._owner_authority
    monkeypatch.setattr(
        authority,
        "_owner_authority",
        lambda *_args: (_ for _ in ()).throw(IntelEventBusy()),
    )
    with pytest.raises(IntelEventBusy):
        batch.eligible(owner_user_id=execution.owner_user_id, event=stored)
    monkeypatch.setattr(authority, "_owner_authority", original)
    assert batch.eligible(owner_user_id=execution.owner_user_id, event=stored)
    db_session.get(Team, uuid.UUID(team["id"])).active = False
    db_session.flush()
    assert not batch.eligible(owner_user_id=execution.owner_user_id, event=stored)


def test_due_query_skips_retained_withdrawn_history(db_session, execution):
    from datetime import datetime, timezone
    from sqlalchemy import text
    from app.services.automation_executions import due_execution_query

    db_session.execute(text('''
        INSERT INTO automation_executions(id,webhook_id,event_id,action_id,policy_state,next_check_at)
        SELECT md5(('retained-' || i)::text)::uuid, :webhook, :event,
          'retained-' || i, 'withdrawn', now() - interval '1 year'
        FROM generate_series(1,30000) i
    '''), {'webhook': execution.webhook_id, 'event': execution.event_id})
    db_session.execute(text('ANALYZE automation_executions'))
    query = due_execution_query(now=datetime.now(timezone.utc), limit=100)
    sql = str(query.compile(db_session.bind, compile_kwargs={'literal_binds': True}))
    plan = db_session.scalar(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql))[0]['Plan']

    def nodes(node):
        yield node
        for child in node.get('Plans', []):
            yield from nodes(child)

    scans = list(nodes(plan))
    assert any(node.get('Index Name') == 'ix_automation_execution_current_due' for node in scans)
    assert sum(node.get('Rows Removed by Filter', 0) for node in scans) <= 1
    assert plan['Actual Rows'] == 1
