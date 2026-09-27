"""Real database ownership/offboarding and receiver credential boundaries."""

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import delete
from app.models.automation_execution import AutomationExecution
from app.models.automation_receiver import AutomationReceiverCredential
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.integration import IntegrationEvent
from app.models.notification_webhook import NotificationWebhook
from app.models.team import Team
from app.models.user import User
from app.schemas.notification import NotificationWebhookWrite
from app.services.automation_executions import register_execution, reserve_policy_update
from app.services.notification_webhook_storage import build_notification_webhook


@pytest.fixture
def destination(db_session, seed_users):
    group = IAMGroup(key=f"integration-{uuid.uuid4().hex}", name="Managers")
    db_session.add(group)
    db_session.flush()
    team = Team(
        key=f"integration-{uuid.uuid4().hex}",
        name="Response",
        membership_group_id=group.id,
        manager_group_id=group.id,
    )
    db_session.add(team)
    db_session.add_all(
        [
            IAMGroupMembership(group_id=group.id, user_id=seed_users[key].id)
            for key in ("admin", "analyst")
        ]
    )
    webhook = build_notification_webhook(
        seed_users["analyst"].id,
        NotificationWebhookWrite(
            name="SIEM",
            url_template="https://receiver.example/hunt",
            event_type="intel.extraction.ready",
            payload_mode="automation_v1",
        ),
    )
    db_session.add(webhook)
    db_session.flush()
    event = IntegrationEvent(
        event_type="intel.extraction.ready",
        source_type="item",
        idempotency_key=str(uuid.uuid4()),
        payload_json={"action_id": "stable-action"},
    )
    db_session.add(event)
    db_session.flush()
    execution = register_execution(db_session, event=event, webhook=webhook)
    db_session.commit()
    return team, webhook, execution


def adopt(client, headers, destination, role="analyst", revision=1):
    team, webhook, _ = destination
    return client.post(
        f"/teams/{team.id}/integrations/{webhook.id}/adopt",
        headers=headers[role],
        json={"expected_revision": revision},
    )


def issue(client, headers, destination):
    team, webhook, _ = destination
    response = client.post(
        f"/teams/{team.id}/integrations/{webhook.id}/receiver-credentials",
        headers=headers["admin"],
        json={
            "name": "Receiver",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def machine_callback(client, credential, execution):
    return client.post(
        f"/notifications/automation/receivers/executions/{execution.id}/callbacks",
        headers={"Authorization": f"Bearer {credential['token']}"},
        json={
            "callback_id": str(uuid.uuid4()),
            "sequence": 1,
            "external_job_id": "siem-job",
            "status": "accepted",
        },
    )


def test_adoption_is_revision_guarded_and_preserves_identity(
    client, auth_headers, db_session, destination
):
    team, _, execution = destination
    assert adopt(client, auth_headers, destination, role="admin").status_code == 404
    response = adopt(client, auth_headers, destination)
    assert response.status_code == 200, response.text
    assert response.json()["ownership_revision"] == 2
    assert adopt(client, auth_headers, destination).status_code == 409
    db_session.refresh(execution)
    assert execution.team_id == team.id and execution.action_id == "stable-action"
    assert (
        client.get("/notifications/webhooks", headers=auth_headers["analyst"]).json()
        == []
    )
    assert client.get(
        f"/teams/{team.id}/integrations", headers=auth_headers["viewer"]
    ).status_code in (403, 404)
    second = adopt(client, auth_headers, destination, role="admin", revision=2)
    assert second.status_code == 200, second.text
    assert second.json()["custodian_user_id"] != response.json()["custodian_user_id"]


def test_machine_token_is_destination_scoped_and_revocation_immediate(
    client, auth_headers, db_session, destination
):
    assert adopt(client, auth_headers, destination).status_code == 200
    credential = issue(client, auth_headers, destination)
    team, webhook, execution = destination
    response = machine_callback(client, credential, execution)
    assert response.status_code == 200, response.text
    assert "findings" not in response.json()
    assert (
        client.get(
            "/items", headers={"Authorization": f"Bearer {credential['token']}"}
        ).status_code
        == 401
    )
    other = AutomationExecution(
        webhook_id=uuid.uuid4(),
        owner_user_id=execution.owner_user_id,
        event_id=execution.event_id,
        team_id=team.id,
        action_id="other",
    )
    db_session.add(other)
    db_session.commit()
    assert machine_callback(client, credential, other).status_code == 404
    response = client.delete(
        f"/teams/{team.id}/integrations/{webhook.id}/receiver-credentials/{credential['id']}",
        headers=auth_headers["admin"],
    )
    assert response.status_code == 200, response.text
    assert machine_callback(client, credential, execution).status_code == 401
    assert (
        db_session.get(AutomationReceiverCredential, credential["id"]).token_hash
        != credential["token"]
    )


def test_offboarding_preserves_destination_history_and_withdrawals(
    client, auth_headers, db_session, seed_users, destination
):
    assert adopt(client, auth_headers, destination).status_code == 200
    credential = issue(client, auth_headers, destination)
    team, webhook, execution = destination
    webhook_id, execution_id = webhook.id, execution.id
    reserve_policy_update(db_session, execution, reason="Custodian departed")
    db_session.commit()
    db_session.execute(delete(User).where(User.id == seed_users["analyst"].id))
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(NotificationWebhook, webhook_id).user_id is None
    assert db_session.get(AutomationExecution, execution_id).owner_user_id is None
    headers = {"Authorization": f"Bearer {credential['token']}"}
    updates = client.get("/notifications/automation/receivers/updates", headers=headers)
    assert updates.status_code == 200, updates.text
    update = updates.json()["items"][0]
    assert "findings" not in updates.text and "team_id" not in updates.text
    assert (
        client.post(
            f"/notifications/automation/receivers/updates/{update['id']}/ack",
            headers=headers,
        ).status_code
        == 200
    )
    response = adopt(client, auth_headers, destination, role="admin", revision=2)
    assert response.status_code == 200, response.text
    db_session.refresh(execution)
    assert execution.policy_state == "withdrawn"


def test_expiry_and_membership_revocation_fail_closed(
    client, auth_headers, db_session, seed_users, destination
):
    assert adopt(client, auth_headers, destination).status_code == 200
    credential = issue(client, auth_headers, destination)
    db_session.get(AutomationReceiverCredential, credential["id"]).expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    team, _, execution = destination
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == team.manager_group_id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    assert machine_callback(client, credential, execution).status_code == 401
    assert adopt(client, auth_headers, destination, revision=2).status_code == 404


def test_unknown_outcomes_do_not_archive(db_session, destination):
    from app.services.automation_health import (
        archive_execution_history,
        automation_backlog,
    )

    _, _, execution = destination
    now = datetime.now(timezone.utc)
    execution.updated_at = now - timedelta(days=200)
    db_session.flush()
    assert archive_execution_history(db_session, now=now) == 0
    assert (
        automation_backlog(db_session, key="automation_unknown", now=now).status
        == "degraded"
    )
    execution.status, execution.policy_state = "completed", "withdrawn"
    execution.policy_revision = execution.policy_acknowledged_revision = 1
    db_session.flush()
    assert archive_execution_history(db_session, now=now) == 1
    assert execution.archived_at is not None and execution.action_id == "stable-action"


def test_cloned_signing_profile_survives_offboarding_and_adoption(
    client, auth_headers, db_session, seed_users, destination
):
    from app.models.webhook_credential import WebhookCredentialProfile
    from app.services.secret_storage import encrypt_text, decrypt_text

    _, webhook, _ = destination
    profile = WebhookCredentialProfile(
        user_id=seed_users["analyst"].id,
        name="Personal signing",
        auth_type="none",
        signing_secret_encrypted=encrypt_text("s" * 40),
    )
    db_session.add(profile)
    db_session.flush()
    webhook.credential_profile_id = profile.id
    db_session.commit()
    assert adopt(client, auth_headers, destination).status_code == 200
    db_session.refresh(webhook)
    cloned_id = webhook.credential_profile_id
    assert cloned_id != profile.id
    db_session.execute(delete(User).where(User.id == seed_users["analyst"].id))
    db_session.commit()
    db_session.expire_all()
    clone = db_session.get(WebhookCredentialProfile, cloned_id)
    assert (
        clone.user_id is None
        and decrypt_text(clone.signing_secret_encrypted) == "s" * 40
    )
    response = adopt(client, auth_headers, destination, role="admin", revision=2)
    assert response.status_code == 200, response.text
    db_session.refresh(webhook)
    assert (
        decrypt_text(
            db_session.get(
                WebhookCredentialProfile, webhook.credential_profile_id
            ).signing_secret_encrypted
        )
        == "s" * 40
    )


def test_machine_auth_rejects_duplicate_headers_and_query_credentials(
    client, auth_headers, destination
):
    assert adopt(client, auth_headers, destination).status_code == 200
    credential = issue(client, auth_headers, destination)
    header = ("Authorization", f"Bearer {credential['token']}")
    assert (
        client.get(
            "/notifications/automation/receivers/updates", headers=[header, header]
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/notifications/automation/receivers/updates?access_token=anything",
            headers=dict([header]),
        ).status_code
        == 401
    )
