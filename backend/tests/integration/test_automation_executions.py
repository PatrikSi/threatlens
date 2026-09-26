"""Real PostgreSQL callbacks, destination ownership and resumable policy receipts."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import func, select

from app.models.automation_execution import (
    AutomationCallback,
    AutomationExecution,
    AutomationPolicyUpdate,
)
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.integration import IntegrationEvent
from app.models.notification_webhook import NotificationWebhook
from app.schemas.notification import NotificationWebhookWrite
from app.services.automation_executions import register_execution, reconcile_executions
from app.services.intel_events import emit_intel_events
from app.services.notification_webhook_storage import build_notification_webhook


@pytest.fixture
def execution(db_session, seed_users):
    feed = Feed(name="Receiver evidence", url=f"https://source.example/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    item = Item(
        feed_id=feed.id,
        title="Automation evidence",
        url="https://source.example/story",
        dedupe_key=str(uuid.uuid4()),
        content_hash="a" * 64,
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        Article(
            item_id=item.id,
            text="Evidence with no extracted indicators",
            final_url=item.url,
            http_status=200,
        )
    )
    db_session.flush()
    event_id = emit_intel_events(db_session, item_id=item.id, deterministic=True)[0]
    event = db_session.get(IntegrationEvent, event_id)
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="Receiver",
            url_template="https://receiver.example/hunt",
            payload_mode="automation_v1",
            event_type=event.event_type,
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    row = register_execution(db_session, event=event, webhook=webhook)
    db_session.commit()
    return row


def payload(sequence=1, status="accepted", **changes):
    return {
        "callback_id": str(uuid.uuid4()),
        "sequence": sequence,
        "external_job_id": "remote-job-1",
        "status": status,
        **changes,
    }


def callback(client, headers, execution, body):
    return client.post(
        f"/notifications/automation/executions/{execution.id}/callbacks",
        headers=headers,
        json=body,
    )


def test_receipts_replay_exact_content_and_reject_conflicts(
    client, auth_headers, execution, db_session
):
    body = payload()
    response = callback(client, auth_headers["analyst"], execution, body)
    assert response.status_code == 200, response.text
    assert callback(client, auth_headers["analyst"], execution, body).status_code == 200
    assert (
        callback(
            client, auth_headers["analyst"], execution, {**body, "status": "running"}
        ).status_code
        == 409
    )
    assert (
        callback(client, auth_headers["analyst"], execution, payload()).status_code
        == 409
    )
    assert db_session.scalar(select(func.count()).select_from(AutomationCallback)) == 1
    assert (
        callback(
            client,
            auth_headers["analyst"],
            execution,
            payload(2, "completed", findings="One confirmed result"),
        ).status_code
        == 200
    )
    assert (
        callback(
            client, auth_headers["analyst"], execution, payload(3, "failed")
        ).status_code
        == 409
    )


def test_execution_ids_do_not_grant_other_owners_access(
    client, auth_headers, execution
):
    assert (
        callback(client, auth_headers["admin"], execution, payload()).status_code == 404
    )
    listing = client.get(
        "/notifications/automation/executions", headers=auth_headers["admin"]
    )
    assert listing.status_code == 200 and listing.json()["items"] == []
    mine = client.get(
        "/notifications/automation/executions", headers=auth_headers["analyst"]
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["items"][0]["id"] == str(execution.id)


def test_source_change_emits_one_durable_withdrawal_and_keeps_completed_hunt(
    client, auth_headers, execution, db_session
):
    assert (
        callback(
            client,
            auth_headers["analyst"],
            execution,
            payload(1, "completed", findings="Historical result"),
        ).status_code
        == 200
    )
    event = db_session.get(IntegrationEvent, execution.event_id)
    item = db_session.get(Item, event.source_id)
    item.classification_required_version += 1
    db_session.commit()
    assert reconcile_executions(db_session) == 1
    db_session.commit()
    assert reconcile_executions(db_session) == 0
    response = client.get(
        "/notifications/automation/updates", headers=auth_headers["analyst"]
    )
    assert response.status_code == 200, response.text
    update = response.json()["items"][0]
    assert update["event_type"] == "intel.withdrawn"
    assert "Historical result" not in response.text
    ack = f"/notifications/automation/updates/{update['id']}/ack"
    assert client.post(ack, headers=auth_headers["admin"]).status_code == 404
    assert client.post(ack, headers=auth_headers["analyst"]).status_code == 200
    assert client.post(ack, headers=auth_headers["analyst"]).status_code == 200
    assert (
        client.get(
            "/notifications/automation/updates", headers=auth_headers["analyst"]
        ).json()["items"]
        == []
    )
    db_session.refresh(execution)
    assert execution.status == "completed" and execution.findings == "Historical result"
    assert (
        db_session.scalar(select(func.count()).select_from(AutomationPolicyUpdate)) == 1
    )


def test_registration_is_idempotent_and_keeps_independent_destinations(
    execution, db_session
):
    event = db_session.get(IntegrationEvent, execution.event_id)
    webhook = db_session.get(NotificationWebhook, execution.webhook_id)
    assert (
        register_execution(db_session, event=event, webhook=webhook).id == execution.id
    )
    other = build_notification_webhook(
        webhook.user_id,
        NotificationWebhookWrite(
            name="Other",
            url_template="https://other.example/hunt",
            payload_mode="automation_v1",
            event_type=event.event_type,
        ),
    )
    db_session.add(other)
    db_session.flush()
    distinct = register_execution(db_session, event=event, webhook=other)
    assert distinct.id != execution.id and distinct.action_id == execution.action_id


def test_scan_persists_progress_and_is_bounded(execution, db_session):
    event = db_session.get(IntegrationEvent, execution.event_id)
    rows = []
    for index in range(4):
        entry = AutomationExecution(
            webhook_id=execution.webhook_id,
            owner_user_id=execution.owner_user_id,
            event_id=event.id,
            action_id=f"bounded-{index}",
            next_check_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db_session.add(entry)
        rows.append(entry)
    db_session.commit()
    assert reconcile_executions(db_session, limit=2) == 2
    db_session.commit()
    assert reconcile_executions(db_session, limit=2) == 2
    db_session.commit()
    assert reconcile_executions(db_session, limit=2) == 1
    db_session.commit()
    assert reconcile_executions(db_session, limit=2) == 0


def test_findings_attach_once_with_investigation_revision(
    client, auth_headers, execution, db_session
):
    assert (
        callback(
            client,
            auth_headers["analyst"],
            execution,
            payload(1, "completed", findings="Analyst reviewed one finding"),
        ).status_code
        == 200
    )
    created = client.post(
        "/investigations",
        headers=auth_headers["analyst"],
        json={"title": "External hunt", "visibility": "private"},
    )
    assert created.status_code == 201, created.text
    investigation = created.json()
    url = f"/notifications/automation/executions/{execution.id}/findings"
    body = {
        "investigation_id": investigation["id"],
        "expected_investigation_version": investigation["version"],
        "expected_sequence": 1,
    }
    response = client.post(url, headers=auth_headers["analyst"], json=body)
    assert response.status_code == 200, response.text
    assert response.json()["investigation_note_id"]
    assert (
        client.post(url, headers=auth_headers["analyst"], json=body).status_code == 409
    )


def test_new_current_action_is_a_replacement_without_resetting_completed_status(
    client, auth_headers, execution, db_session
):
    assert (
        callback(
            client,
            auth_headers["analyst"],
            execution,
            payload(1, "completed", findings="Keep this result"),
        ).status_code
        == 200
    )
    event = db_session.get(IntegrationEvent, execution.event_id)
    item = db_session.get(Item, event.source_id)
    item.classification_required_version += 1
    db_session.flush()
    new_event_id = emit_intel_events(db_session, item_id=item.id, deterministic=True)[0]
    replacement = register_execution(
        db_session,
        event=db_session.get(IntegrationEvent, new_event_id),
        webhook=db_session.get(NotificationWebhook, execution.webhook_id),
    )
    db_session.commit()
    reconcile_executions(db_session)
    db_session.commit()
    update = db_session.scalar(
        select(AutomationPolicyUpdate).where(
            AutomationPolicyUpdate.execution_id == execution.id
        )
    )
    assert update.event_type == "intel.replaced"
    assert update.replacement_action_id == replacement.action_id
    db_session.refresh(execution)
    assert execution.status == "completed"
    assert execution.findings == "Keep this result"


def test_deleting_destination_does_not_erase_execution_receipts(execution, db_session):
    db_session.delete(db_session.get(NotificationWebhook, execution.webhook_id))
    db_session.commit()
    assert db_session.get(AutomationExecution, execution.id) is not None
