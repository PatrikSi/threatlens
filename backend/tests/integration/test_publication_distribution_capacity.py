"""Acknowledged replay history cannot stall healthy consumers indefinitely."""

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select

from app.models.indicator_publication import IndicatorPublication
from app.models.publication_consumer import (
    PublicationChange,
    PublicationConsumer,
    PublicationSubscription,
)
from app.services import publication_consumers as service
from app.services.secret_storage import decrypt_json, encrypt_json
from tests.integration.test_indicator_publications import (
    reviewed as reviewed_fixture,
    publish,
)
from tests.integration.test_indicator_intelligence import intel_setup  # noqa: F401
from tests.integration.test_publication_distribution import register

reviewed = reviewed_fixture


def _history(client, reviewed, auth_headers, db, *, count, acknowledged):
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, headers = register(client, reviewed, auth_headers)
    subscribed = client.post(
        f"{path}/{consumer['id']}/subscriptions",
        json={"publication_id": publication["id"]},
        headers=auth_headers["admin"],
    )
    assert subscribed.status_code == 204, subscribed.text
    row = db.get(PublicationConsumer, uuid.UUID(consumer["id"]))
    first = db.scalar(
        select(PublicationChange).where(PublicationChange.consumer_id == row.id)
    )
    now = datetime.now(timezone.utc)
    if acknowledged:
        first.acknowledged_at = now
    db.add_all(
        PublicationChange(
            id=uuid.uuid4(),
            consumer_id=row.id,
            sequence=sequence,
            publication_id=uuid.UUID(publication["id"]),
            revision=1,
            kind="changed",
            created_at=now,
            acknowledged_at=now if acknowledged else None,
        )
        for sequence in range(2, count + 1)
    )
    row.sequence = count
    db.commit()
    return row, publication, path, headers


def test_capacity_compaction_advances_only_an_acknowledged_prefix(
    client, reviewed, auth_headers, db_session, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(service, "SOFT_RETAINED_CHANGES", 2)
    monkeypatch.setattr(service, "MAX_PENDING_CHANGES", 3)
    row, publication, _, headers = _history(
        client, reviewed, auth_headers, db_session, count=4, acknowledged=True
    )
    assert service.prune_acknowledged_changes(db_session, row) == 2
    assert row.replay_floor == 2
    db_session.commit()
    assert (
        client.get(
            "/publication-distribution/changes", headers=headers, params={"after": 0}
        ).status_code
        == 410
    )
    page = client.get(
        "/publication-distribution/changes", headers=headers, params={"after": 2}
    )
    assert page.status_code == 200, page.text
    assert page.json()["capacity_compaction_enabled"]
    assert (
        page.json()["retained_changes"] == 2
        and not page.json()["retention_backpressure"]
    )
    assert [entry["sequence"] for entry in page.json()["changes"]] == [3, 4]
    current = db_session.get(IndicatorPublication, uuid.UUID(publication["id"]))
    current.revision += 1
    db_session.get(PublicationSubscription, (row.id, current.id)).next_check_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    db_session.commit()
    changed = client.get(
        "/publication-distribution/changes", headers=headers, params={"after": 4}
    )
    assert (
        changed.status_code == 200 and changed.json()["changes"][0]["kind"] == "changed"
    )


def test_unacknowledged_prefix_is_retained_and_withdrawals_bypass_backpressure(
    client, reviewed, auth_headers, db_session, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(service, "SOFT_RETAINED_CHANGES", 2)
    monkeypatch.setattr(service, "MAX_PENDING_CHANGES", 3)
    row, publication, _, headers = _history(
        client, reviewed, auth_headers, db_session, count=3, acknowledged=False
    )
    assert service.prune_acknowledged_changes(db_session, row) == 0
    assert row.replay_floor == 0
    db_session.commit()
    page = client.get("/publication-distribution/changes", headers=headers)
    assert page.status_code == 200 and page.json()["retention_backpressure"]
    row.principal_id = None
    db_session.get(
        PublicationSubscription, (row.id, uuid.UUID(publication["id"]))
    ).next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    withdrawn = client.get(
        "/publication-distribution/changes", headers=headers, params={"after": 3}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert (
        len(withdrawn.json()["changes"]) == 1
        and withdrawn.json()["changes"][0]["kind"] == "withdrawn"
    )
    assert withdrawn.json()["retained_changes"] == 4
    assert service.prune_acknowledged_changes(db_session, row) == 0


def test_history_pressure_does_not_admit_unbounded_new_subscriptions(
    client, reviewed, auth_headers, db_session, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(service, "SOFT_RETAINED_CHANGES", 2)
    monkeypatch.setattr(service, "MAX_PENDING_CHANGES", 3)
    row, _, path, _ = _history(
        client, reviewed, auth_headers, db_session, count=3, acknowledged=False
    )
    _, _, publication = publish(client, reviewed, auth_headers)
    response = client.post(
        f"{path}/{row.id}/subscriptions",
        headers=auth_headers["admin"],
        json={"publication_id": publication["id"]},
    )
    assert (
        response.status_code == 429
        and "consumer_retention_backpressure" in response.text
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(PublicationSubscription)
            .where(PublicationSubscription.consumer_id == row.id)
        )
        == 1
    )


def test_subscriptions_cannot_exceed_original_captured_clearance(
    client, reviewed, auth_headers, db_session
):  # noqa: F811
    _, _, publication = publish(client, reviewed, auth_headers)
    path, consumer, _ = register(client, reviewed, auth_headers)
    row = db_session.get(PublicationConsumer, uuid.UUID(consumer["id"]))
    snapshot = decrypt_json(row.authorization_encrypted)
    snapshot["enforced"] = True
    snapshot["allowed_label_ids"] = []
    row.authorization_encrypted = encrypt_json(snapshot)
    db_session.commit()
    response = client.post(
        f"{path}/{row.id}/subscriptions",
        headers=auth_headers["admin"],
        json={"publication_id": publication["id"]},
    )
    assert response.status_code == 403 and "consumer_clearance_denied" in response.text
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(PublicationChange)
            .where(PublicationChange.consumer_id == row.id)
        )
        == 0
    )


def test_lost_registration_response_retains_one_consumer_and_returns_recovery_identity(
    client, reviewed, auth_headers, db_session
):  # noqa: F811
    path = f"/teams/{reviewed[0]['id']}/publication-consumers"
    payload = {"name": "Durable receiver", "idempotency_key": str(uuid.uuid4())}
    created = client.post(path, headers=auth_headers["admin"], json=payload)
    assert created.status_code == 201, created.text
    repeated = client.post(path, headers=auth_headers["admin"], json=payload)
    assert (
        repeated.status_code == 409 and "consumer_already_registered" in repeated.text
    )
    assert (
        created.json()["id"] in repeated.text
        and created.json()["token"] not in repeated.text
    )
    conflicting = client.post(
        path,
        headers=auth_headers["admin"],
        json={**payload, "name": "Different receiver"},
    )
    assert (
        conflicting.status_code == 409
        and "consumer_request_conflict" in conflicting.text
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(PublicationConsumer)
            .where(PublicationConsumer.team_id == uuid.UUID(reviewed[0]["id"]))
        )
        == 1
    )


def test_retirement_keeps_acknowledgement_authority_until_history_can_be_archived(
    client, reviewed, auth_headers, db_session
):  # noqa: F811
    row, publication, path, headers = _history(
        client, reviewed, auth_headers, db_session, count=1, acknowledged=False
    )
    manage = f"{path}/{row.id}"
    premature = client.post(f"{manage}/archive", headers=auth_headers["admin"])
    assert (
        premature.status_code == 409
        and "consumer_retirement_required" in premature.text
    )
    for _ in range(2):
        retired = client.post(f"{manage}/retire", headers=auth_headers["admin"])
        assert retired.status_code == 204, retired.text
    feed = client.get("/publication-distribution/changes", headers=headers)
    assert feed.status_code == 200, feed.text
    changes = feed.json()["changes"]
    assert [change["kind"] for change in changes] == ["available", "withdrawn"]
    rejected = client.post(
        f"{manage}/subscriptions",
        headers=auth_headers["admin"],
        json={"publication_id": publication["id"]},
    )
    assert rejected.status_code == 409 and "consumer_retired" in rejected.text
    blocked = client.post(f"{manage}/archive", headers=auth_headers["admin"])
    assert (
        blocked.status_code == 409
        and "consumer_acknowledgements_pending" in blocked.text
    )
    acknowledged = client.post(
        "/publication-distribution/acknowledgements",
        headers=headers,
        json={"generation": 1, "change_ids": [change["id"] for change in changes]},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    archived = client.post(f"{manage}/archive", headers=auth_headers["admin"])
    assert archived.status_code == 204, archived.text
    db_session.expire_all()
    assert db_session.get(PublicationConsumer, row.id) is None
    assert db_session.get(IndicatorPublication, uuid.UUID(publication["id"]))
    for model in (PublicationChange, PublicationSubscription):
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.consumer_id == row.id)
            )
            == 0
        )
    assert (
        client.get("/publication-distribution/changes", headers=headers).status_code
        == 401
    )


def test_retired_receiver_must_acknowledge_current_withdrawal_ids_before_archiving(
    client, reviewed, auth_headers, db_session
):  # noqa: F811
    row, _, path, headers = _history(
        client, reviewed, auth_headers, db_session, count=1, acknowledged=False
    )
    manage = f"{path}/{row.id}"
    assert (
        client.post(f"{manage}/retire", headers=auth_headers["admin"]).status_code
        == 204
    )
    reset = client.post(
        "/publication-distribution/reset",
        headers=headers,
        json={"expected_generation": 1, "discarded_previous_publications": True},
    )
    assert reset.status_code == 409, reset.text
    assert "consumer_retired_reset_forbidden" in reset.text
    feed = client.get("/publication-distribution/changes", headers=headers)
    assert feed.status_code == 200 and len(feed.json()["changes"]) == 2
    assert (
        client.post(f"{manage}/archive", headers=auth_headers["admin"]).status_code
        == 409
    )
    ack = client.post(
        "/publication-distribution/acknowledgements",
        headers=headers,
        json={
            "generation": 1,
            "change_ids": [entry["id"] for entry in feed.json()["changes"]],
        },
    )
    assert ack.status_code == 200, ack.text
    assert (
        client.post(f"{manage}/archive", headers=auth_headers["admin"]).status_code
        == 204
    )


def test_capacity_compaction_bounds_each_transaction_to_one_hundred_rows(
    client, reviewed, auth_headers, db_session, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(service, "SOFT_RETAINED_CHANGES", 2)
    monkeypatch.setattr(service, "MAX_PENDING_CHANGES", 3)
    row, _, _, _ = _history(
        client, reviewed, auth_headers, db_session, count=202, acknowledged=True
    )
    assert service.prune_acknowledged_changes(db_session, row) == 100
    assert row.replay_floor == 100
    assert service.prune_acknowledged_changes(db_session, row) == 100
    assert row.replay_floor == 200
    assert service.prune_acknowledged_changes(db_session, row) == 0
