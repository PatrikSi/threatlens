"""Consumer acknowledgements survive evidence loss without disclosing evidence."""

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import delete, select

from app.models.iam import IAMGroupMembership
from app.models.publication_consumer import PublicationChange, PublicationConsumer
from tests.integration.test_indicator_publications import reviewed, publish  # noqa: F401
from tests.integration.test_indicator_intelligence import intel_setup  # noqa: F401


def register(client, reviewed, auth_headers):  # noqa: F811
    team = reviewed[0]
    path = f"/teams/{team['id']}/publication-consumers"
    response = client.post(
        path,
        json={"name": "Test consumer", "idempotency_key": str(uuid.uuid4())},
        headers=auth_headers["admin"],
    )
    assert response.status_code == 201, response.text
    row = response.json()
    return path, row, {"Authorization": "Bearer " + row["token"]}


def test_consumer_delivery_replay_and_idempotent_ack(client, reviewed, auth_headers):  # noqa: F811
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, headers = register(client, reviewed, auth_headers)
    subscribe = f"{path}/{consumer['id']}/subscriptions"
    for _ in range(2):
        response = client.post(
            subscribe,
            json={"publication_id": publication["id"]},
            headers=auth_headers["admin"],
        )
        assert response.status_code == 204, response.text
    response = client.get("/publication-distribution/changes", headers=headers)
    assert response.status_code == 200, response.text
    feed = response.json()
    assert len(feed["changes"]) == 1
    change = feed["changes"][0]
    assert change["kind"] == "available" and "evil.net" not in response.text
    for _ in range(2):
        response = client.post(
            "/publication-distribution/acknowledgements",
            json={"generation": 1, "change_ids": [change["id"]]},
            headers=headers,
        )
        assert response.status_code == 200 and response.json()["acknowledged"] == 1
    # A consumer secret grants no article download or general API authority.
    assert (
        client.get(
            f"/teams/{reviewed[0]['id']}/indicator-publications/{publication['id']}/download",
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/publication-distribution/changes", params={"after": 1}, headers=headers
        ).json()["changes"]
        == []
    )


def test_membership_loss_produces_opaque_withdrawal_and_cannot_revive(
    client, reviewed, auth_headers, db_session, seed_users
):  # noqa: F811
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, headers = register(client, reviewed, auth_headers)
    assert (
        client.post(
            f"{path}/{consumer['id']}/subscriptions",
            json={"publication_id": publication["id"]},
            headers=auth_headers["admin"],
        ).status_code
        == 204
    )
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.user_id == seed_users["admin"].id
        )
    )
    # Explicitly removing the credential custodian is sufficient even if admin
    # policy grants broad team membership; old token still owes withdrawals.
    row = db_session.get(PublicationConsumer, uuid.UUID(consumer["id"]))
    row.principal_id = None
    db_session.commit()
    result = client.get(
        "/publication-distribution/changes", params={"after": 1}, headers=headers
    )
    assert result.status_code == 200, result.text
    changes = result.json()["changes"]
    assert len(changes) == 1 and changes[0]["kind"] == "withdrawn"
    assert "evil.net" not in result.text and "title" not in result.text
    assert (
        client.post(
            "/publication-distribution/acknowledgements",
            json={"generation": 1, "change_ids": [changes[0]["id"]]},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/publication-distribution/changes", params={"after": 2}, headers=headers
        ).json()["changes"]
        == []
    )


def test_cross_consumer_ack_and_rotated_credentials_are_rejected(
    client, reviewed, auth_headers
):  # noqa: F811
    _, _, publication = publish(client, reviewed, auth_headers)
    path, one, headers = register(client, reviewed, auth_headers)
    client.post(
        f"{path}/{one['id']}/subscriptions",
        json={"publication_id": publication["id"]},
        headers=auth_headers["admin"],
    )
    change = client.get("/publication-distribution/changes", headers=headers).json()[
        "changes"
    ][0]
    _, _, other_headers = register(client, reviewed, auth_headers)
    assert (
        client.post(
            "/publication-distribution/acknowledgements",
            json={"generation": 1, "change_ids": [change["id"]]},
            headers=other_headers,
        ).status_code
        == 404
    )
    rotated = client.post(f"{path}/{one['id']}/rotate", headers=auth_headers["admin"])
    assert rotated.status_code == 200, rotated.text
    assert (
        client.get("/publication-distribution/changes", headers=headers).status_code
        == 401
    )
    assert (
        client.get(
            "/publication-distribution/changes",
            headers={"Authorization": "Bearer " + rotated.json()["token"]},
        ).status_code
        == 200
    )


def test_replay_expiry_requires_explicit_discard_reset(
    client, reviewed, auth_headers, db_session
):  # noqa: F811
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, headers = register(client, reviewed, auth_headers)
    client.post(
        f"{path}/{consumer['id']}/subscriptions",
        json={"publication_id": publication["id"]},
        headers=auth_headers["admin"],
    )
    change = db_session.scalar(
        select(PublicationChange).where(
            PublicationChange.consumer_id == uuid.UUID(consumer["id"])
        )
    )
    change.created_at = datetime.now(timezone.utc) - timedelta(days=91)
    change.acknowledged_at = change.created_at
    db_session.commit()
    # A request which discovers the gap must persist retention independently;
    # this direct maintenance invocation mirrors the minute sweeper.
    from app.services.publication_consumers import prune_acknowledged_changes

    row = db_session.get(PublicationConsumer, uuid.UUID(consumer["id"]))
    assert prune_acknowledged_changes(db_session, row) == 1
    db_session.commit()
    assert (
        client.get("/publication-distribution/changes", headers=headers).status_code
        == 410
    )
    assert (
        client.post(
            "/publication-distribution/reset",
            json={"expected_generation": 1, "discarded_previous_publications": False},
            headers=headers,
        ).status_code
        == 409
    )
    reset = client.post(
        "/publication-distribution/reset",
        json={"expected_generation": 1, "discarded_previous_publications": True},
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"generation": 2, "after": 1}
    page = client.get(
        "/publication-distribution/changes",
        params={"generation": 2, "after": 1},
        headers=headers,
    )
    assert page.status_code == 200, page.text
    assert page.json()["changes"][0]["publication_id"] == publication["id"]
    assert (
        client.post(
            "/publication-distribution/acknowledgements",
            json={"generation": 1, "change_ids": [str(uuid.uuid4())]},
            headers=headers,
        ).status_code
        == 409
    )
