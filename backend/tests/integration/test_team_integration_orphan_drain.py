"""Deleted destinations retain a scoped human path to settle withdrawals."""

from datetime import datetime, timezone

from sqlalchemy import delete

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.automation_receiver import AutomationReceiverCredential
from app.models.notification_webhook import NotificationWebhook
from app.services.automation_executions import reserve_policy_update
from tests.integration.test_team_integrations import adopt, destination, issue  # noqa: F401


def test_team_operator_can_drain_after_destination_removal_and_machine_revocation(
    client,
    auth_headers,
    db_session,
    seed_users,
    destination,  # noqa: F811
):
    assert adopt(client, auth_headers, destination).status_code == 200
    credential = issue(client, auth_headers, destination)
    _, webhook, execution = destination
    update = reserve_policy_update(db_session, execution, reason="Restore quarantine")
    db_session.flush()
    update_id, execution_id = update.id, execution.id
    db_session.get(
        AutomationReceiverCredential, credential["id"]
    ).revoked_at = datetime.now(timezone.utc)
    db_session.execute(
        delete(NotificationWebhook).where(NotificationWebhook.id == webhook.id)
    )
    value, prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=seed_users["admin"].id,
            name="Restore withdrawal drain",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=["read:teams", "read:notifications", "write:notifications"],
        )
    )
    db_session.commit()
    revoked = client.get(
        "/notifications/automation/receivers/updates",
        headers={"Authorization": "Bearer " + credential["token"]},
    )
    assert revoked.status_code == 401
    headers = {"Authorization": "Bearer " + value}
    listing = client.get("/notifications/automation/updates", headers=headers)
    assert listing.status_code == 200, listing.text
    assert [row["id"] for row in listing.json()["items"]] == [str(update_id)]
    assert "findings" not in listing.text and "indicators" not in listing.text
    denied = client.get("/notifications/automation/executions", headers=headers)
    assert denied.status_code == 403  # No read:items authority was granted.
    acknowledged = client.post(
        f"/notifications/automation/updates/{update_id}/ack", headers=headers
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["execution_id"] == str(execution_id)
    assert (
        client.get("/notifications/automation/updates", headers=headers).json()["items"]
        == []
    )
