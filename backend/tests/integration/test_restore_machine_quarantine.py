"""Execute the packaged recovery SQL against the disposable migrated PostgreSQL."""
# ruff: noqa: F811 -- Imported pytest fixtures are injected by their public names.

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import select, text

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.automation_execution import AutomationPolicyUpdate
from app.models.indicator_publication import IndicatorPublication
from app.models.publication_consumer import PublicationChange, PublicationConsumer
from app.services.automation_executions import reserve_policy_update
from tests.integration.test_indicator_publications import reviewed, publish  # noqa: F401
from tests.integration.test_indicator_intelligence import intel_setup  # noqa: F401
from tests.integration.test_publication_distribution import register
from tests.integration.test_team_integrations import destination, adopt, issue  # noqa: F401


def _phase(db, phase):
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts/recovery/post_restore_quarantine.sh").exists()
    )
    script = (root / "scripts/recovery/post_restore_quarantine.sh").read_text()
    delimiter = f"${phase}$"
    sql = (
        "DO "
        + delimiter
        + script.split("DO " + delimiter, 1)[1].split(delimiter + ";", 1)[0]
        + delimiter
        + ";"
    )
    db.execute(text(sql))


def _quarantine(db):
    db.execute(
        text("SELECT set_config('threatlens.restore_checksum', :checksum, false)"),
        {"checksum": "ab" * 32},
    )
    db.execute(
        text("SELECT set_config('threatlens.restore_audit_id', :audit, false)"),
        {"audit": str(uuid.uuid4())},
    )
    _phase(db, "preflight")
    _phase(db, "quarantine")
    _phase(db, "verify")
    db.commit()
    db.expire_all()


def _fresh_admin(db, user):
    # A deliberately newly issued credential models administrator recovery login.
    value, prefix, digest = generate_api_token()
    db.add(
        ApiToken(
            user_id=user.id,
            name="recovery-admin",
            token_prefix=prefix,
            token_hash=digest,
            scopes=["*:*"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    return {"Authorization": "Bearer " + value}


@pytest.mark.parametrize("format", ["stix", "misp"])
def test_restored_consumers_reject_old_secrets_ack_and_reset_then_drain(
    client,
    reviewed,
    auth_headers,
    db_session,
    seed_users,
    format,  # noqa: F811
):  # noqa: F811
    path, _, publication = publish(client, reviewed, auth_headers, format)
    manage_path, consumer, old_headers = register(client, reviewed, auth_headers)
    manage = f"{manage_path}/{consumer['id']}"
    assert (
        client.post(
            f"{manage}/subscriptions",
            json={"publication_id": publication["id"]},
            headers=auth_headers["admin"],
        ).status_code
        == 204
    )
    old_changes = client.get(
        "/publication-distribution/changes", headers=old_headers
    ).json()["changes"]
    old_ids = {entry["id"] for entry in old_changes}
    row = db_session.get(PublicationConsumer, uuid.UUID(consumer["id"]))
    # Counter rollback can collide with a reset accepted after the backup.
    old_reset = {"expected_generation": 3, "discarded_previous_publications": True}
    _quarantine(db_session)
    first_ids = set(db_session.scalars(select(PublicationChange.id)))
    _quarantine(db_session)
    second_ids = set(db_session.scalars(select(PublicationChange.id)))
    assert len(first_ids) == len(second_ids) == 2
    assert first_ids.isdisjoint(second_ids)
    assert old_ids.isdisjoint(str(identity) for identity in second_ids)
    assert (
        row.generation == 3
        and row.retired_at is not None
        and row.revoked_at is not None
    )
    assert (
        client.get("/publication-distribution/status", headers=old_headers).status_code
        == 401
    )

    fresh_admin = _fresh_admin(db_session, seed_users["admin"])
    rotated = client.post(f"{manage}/rotate", headers=fresh_admin)
    assert rotated.status_code == 200, rotated.text
    current_headers = {"Authorization": "Bearer " + rotated.json()["token"]}
    status = client.get(
        "/publication-distribution/status", headers=current_headers
    ).json()
    # An old receiver cursor/generation must not silently look caught up.
    stale = client.get(
        "/publication-distribution/changes",
        headers=current_headers,
        params={"generation": 99, "after": 999},
    )
    assert stale.status_code == 410
    assert (
        "publication-distribution/status" in stale.text and "cannot reset" in stale.text
    )
    reset = client.post(
        "/publication-distribution/reset", headers=current_headers, json=old_reset
    )
    assert reset.status_code == 409 and "consumer_retired_reset_forbidden" in reset.text
    ack = client.post(
        "/publication-distribution/acknowledgements",
        headers=current_headers,
        json={"generation": status["generation"], "change_ids": list(old_ids)},
    )
    assert ack.status_code == 404
    feed = client.get(
        "/publication-distribution/changes",
        headers=current_headers,
        params={"generation": status["generation"], "after": status["replay_floor"]},
    )
    assert feed.status_code == 200, feed.text
    changes = feed.json()["changes"]
    assert len(changes) == 2 and changes[-1]["kind"] == "withdrawn"
    assert all(not change["acknowledged"] for change in changes)
    assert (
        client.post(
            f"{manage}/subscriptions",
            json={"publication_id": publication["id"]},
            headers=fresh_admin,
        ).status_code
        == 409
    )

    artifact = client.get(f"{path}/{publication['id']}/download", headers=fresh_admin)
    assert artifact.status_code == 200, artifact.text
    stored = db_session.get(IndicatorPublication, uuid.UUID(publication["id"]))
    assert (
        stored.status == "withdrawn"
        and stored.revision == 2
        and stored.withdrawn_count == stored.indicator_count
    )
    assert all(
        entry["withdrawal_reason"] == "restore_quarantine"
        for entry in stored.snapshot_json["indicators"]
    )
    if format == "stix":
        assert all(
            entry["revoked"]
            for entry in artifact.json()["objects"]
            if entry["type"] == "indicator"
        )
    else:
        assert all(
            entry["deleted"] and not entry["to_ids"]
            for entry in artifact.json()["response"][0]["Event"]["Attribute"]
        )
    ack = client.post(
        "/publication-distribution/acknowledgements",
        headers=current_headers,
        json={
            "generation": status["generation"],
            "change_ids": [entry["id"] for entry in changes],
        },
    )
    assert ack.status_code == 200, ack.text
    assert client.post(f"{manage}/archive", headers=fresh_admin).status_code == 204
    assert db_session.get(IndicatorPublication, stored.id) is not None


def test_restored_receiver_credentials_and_old_policy_ack_ids_are_invalid(
    client,
    auth_headers,
    db_session,
    seed_users,
    destination,  # noqa: F811
):  # noqa: F811
    team, _, execution = destination
    assert adopt(client, auth_headers, destination).status_code == 200
    old_credential = issue(client, auth_headers, destination)
    # Include an already-withdrawn obligation; no fresh action is created.
    update = reserve_policy_update(db_session, execution, reason="Previously withdrawn")
    db_session.commit()
    old_update_id, action_id, execution_id = (
        update.id,
        execution.action_id,
        execution.id,
    )
    _quarantine(db_session)
    _quarantine(db_session)
    assert execution.id == execution_id and execution.action_id == action_id
    assert execution.policy_state == "withdrawn"
    old_headers = {"Authorization": "Bearer " + old_credential["token"]}
    assert (
        client.get(
            "/notifications/automation/receivers/updates", headers=old_headers
        ).status_code
        == 401
    )
    fresh_admin = _fresh_admin(db_session, seed_users["admin"])
    new_credential = issue(client, {"admin": fresh_admin}, destination)
    headers = {"Authorization": "Bearer " + new_credential["token"]}
    assert (
        client.post(
            f"/notifications/automation/receivers/updates/{old_update_id}/ack",
            headers=headers,
        ).status_code
        == 404
    )
    # Start paging from the beginning even if the receiver retained a later cursor.
    page = client.get("/notifications/automation/receivers/updates", headers=headers)
    assert page.status_code == 200, page.text
    updates = page.json()["items"]
    assert len(updates) == 1 and updates[0]["action_id"] == action_id
    assert updates[0]["id"] != str(old_update_id)
    assert (
        client.post(
            f"/notifications/automation/receivers/updates/{updates[0]['id']}/ack",
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/notifications/automation/receivers/updates", headers=headers
        ).json()["items"]
        == []
    )
    assert (
        db_session.scalar(select(AutomationPolicyUpdate.execution_id)) == execution_id
    )


def test_already_withdrawn_consumer_history_survives_with_fresh_acknowledgement_ids(
    client, reviewed, auth_headers, db_session
):
    path, _, publication = publish(client, reviewed, auth_headers)
    manage_path, consumer, headers = register(client, reviewed, auth_headers)
    manage = f"{manage_path}/{consumer['id']}"
    assert (
        client.post(
            f"{manage}/subscriptions",
            json={"publication_id": publication["id"]},
            headers=auth_headers["admin"],
        ).status_code
        == 204
    )
    assert (
        client.post(f"{manage}/retire", headers=auth_headers["admin"]).status_code
        == 204
    )
    changes = client.get("/publication-distribution/changes", headers=headers).json()[
        "changes"
    ]
    assert (
        client.post(
            "/publication-distribution/acknowledgements",
            headers=headers,
            json={"generation": 1, "change_ids": [changes[0]["id"]]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{path}/{publication['id']}/withdraw",
            headers=auth_headers["admin"],
            json={"expected_revision": 1},
        ).status_code
        == 200
    )
    stored = db_session.get(IndicatorPublication, uuid.UUID(publication["id"]))
    snapshot, revision = stored.snapshot_json, stored.revision
    _quarantine(db_session)
    assert stored.snapshot_json == snapshot and stored.revision == revision
    rows = db_session.scalars(
        select(PublicationChange).order_by(PublicationChange.sequence)
    ).all()
    assert len(rows) == 2
    assert {str(row.id) for row in rows}.isdisjoint(change["id"] for change in changes)
    assert rows[0].acknowledged_at is not None and rows[1].acknowledged_at is None
    assert rows[1].kind == "withdrawn"
