"""Real database/API transitions for extraction and team intelligence reviews."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.models.iam import IAMGroupMembership
from app.models.integration import IntegrationEvent
from app.models.intel_assessment import IndicatorAssessment, ItemIntelState
from app.models.ioc import ItemIOC
from app.services.intel_events import emit_intel_events
from app.services.ioc_extraction import extract_iocs
from app.services.ioc_storage import replace_item_iocs
from tests.integration.test_investigations_api import _create_item
from tests.integration.test_teams_api import _team


def _extract(db, item, text="Threat infrastructure: evil[.]net and example[.]com."):
    replace_item_iocs(
        db,
        item_id=item.id,
        extracted=extract_iocs(title=text, summary=None, article_text=None),
    )
    return emit_intel_events(db, item_id=item.id, deterministic=True)


@pytest.fixture()
def intel_setup(client, db_session, seed_users, auth_headers):
    team, members, managers = _team(client, db_session, seed_users, auth_headers)
    item = _create_item(db_session)
    _extract(db_session, item)
    db_session.commit()
    return team, item, members, managers


def _page(client, setup, headers):
    team, item, *_ = setup
    response = client.get(
        f"/items/{item.id}/indicators", params={"team_id": team["id"]}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _command(page, *, version=0, verdict="malicious"):
    return {
        "expected_version": version,
        "source_revision": page["source_revision"],
        "extraction_revision": page["extraction_revision"],
        "verdict": verdict,
        "reason": "Reviewed the primary source.",
        "expires_at": None,
    }


def test_transactional_events_deduplicate_unchanged_sets_and_rollback(db_session):
    item = _create_item(db_session)
    first = _extract(db_session, item)
    assert len(first) == 2
    assert _extract(db_session, item) == []
    events = db_session.scalars(
        select(IntegrationEvent).where(IntegrationEvent.id.in_(first))
    ).all()
    assert {row.event_type for row in events} == {
        "intel.extraction.ready",
        "intel.indicators.changed",
    }
    payload = events[0].payload_json
    assert payload["indicators_complete"] is True
    assert payload["source_revision"] == item.classification_required_version
    evil = next(row for row in payload["indicators"] if row["value"] == "evil.net")
    assert evil["maliciousness_confidence"] is None
    assert evil["evidence"][0]["raw"] == "evil[.]net"
    assert evil["evidence"][0]["transformations"]
    old_revision = db_session.get(ItemIntelState, item.id).revision
    with pytest.raises(RuntimeError):
        with db_session.begin_nested():
            assert len(_extract(db_session, item, "other.net")) == 2
            raise RuntimeError("interrupted before commit")
    assert db_session.get(ItemIntelState, item.id).revision == old_revision
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(IntegrationEvent)
            .where(IntegrationEvent.source_id == str(item.id))
        )
        == 2
    )
    item.classification_required_version += 1
    db_session.flush()
    # A new source revision completes extraction again but does not resend an
    # unchanged indicator set as an indicator-change event.
    next_events = _extract(db_session, item)
    assert [
        db_session.get(IntegrationEvent, identity).event_type
        for identity in next_events
    ] == ["intel.extraction.ready"]


def test_oversized_indicator_events_explicitly_withhold_the_set(db_session):
    item = _create_item(db_session)
    events = _extract(
        db_session, item, " ".join(f"ioc{index}.net" for index in range(251))
    )
    payload = db_session.get(IntegrationEvent, events[0]).payload_json
    assert payload["indicator_count"] == 251
    assert payload["indicators"] == []
    assert payload["indicators_complete"] is False
    assert payload["incomplete_reason"] == "indicator_count_limit"


def test_analyst_review_conflicts_expiry_retraction_history_and_refresh(
    client, db_session, auth_headers, intel_setup
):
    team, item, *_ = intel_setup
    page = _page(client, intel_setup, auth_headers["analyst"])
    assert page["can_review"] is True
    assert page["can_manage_suppressions"] is False
    indicator = next(row for row in page["items"] if row["value"] == "evil.net")
    assert indicator["ai"] is None and indicator["extraction_confidence"] == 0.95
    endpoint = (
        f"/items/{item.id}/indicators/{indicator['id']}/assessment?team_id={team['id']}"
    )
    response = client.patch(
        endpoint, json=_command(page), headers=auth_headers["analyst"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert (
        client.patch(
            endpoint, json=_command(page), headers=auth_headers["analyst"]
        ).status_code
        == 409
    )
    command = _command(page, version=1, verdict="retracted")
    result = client.patch(endpoint, json=command, headers=auth_headers["analyst"])
    assert result.status_code == 200, result.text
    updated = _page(client, intel_setup, auth_headers["analyst"])
    entry = next(row for row in updated["items"] if row["id"] == indicator["id"])
    assert "analyst_retracted" in entry["exclusion_reasons"]
    history = client.get(
        endpoint.replace("?", "/history?"), headers=auth_headers["analyst"]
    )
    assert history.status_code == 200, history.text
    assert [entry["snapshot"]["verdict"] for entry in history.json()["items"]] == [
        "retracted",
        "malicious",
    ]
    row = db_session.scalar(
        select(IndicatorAssessment).where(IndicatorAssessment.item_id == item.id)
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    expired_page = _page(client, intel_setup, auth_headers["analyst"])
    entry = next(row for row in expired_page["items"] if row["id"] == indicator["id"])
    assert entry["assessment"]["expired"] is True and entry["excluded"] is False
    item.classification_required_version += 1
    db_session.commit()
    stale_page = _page(client, intel_setup, auth_headers["analyst"])
    assert (
        stale_page["extraction_current"] is False and stale_page["can_review"] is False
    )
    assert (
        client.patch(
            endpoint,
            json=_command(stale_page, version=2),
            headers=auth_headers["analyst"],
        ).status_code
        == 409
    )


def test_suppression_requires_manager_and_current_membership(
    client, db_session, auth_headers, seed_users, intel_setup
):
    team, item, members, _ = intel_setup
    endpoint = f"/teams/{team['id']}/indicator-suppressions"
    command = {
        "ioc_type": "domain",
        "value": "evil[.]net",
        "reason": "Approved testing infrastructure.",
        "active": True,
    }
    assert (
        client.post(endpoint, json=command, headers=auth_headers["analyst"]).status_code
        == 404
    )
    created = client.post(endpoint, json=command, headers=auth_headers["admin"])
    assert created.status_code == 201, created.text
    assert created.json()["value"] == "evil.net"
    assert (
        client.post(endpoint, json=command, headers=auth_headers["admin"]).status_code
        == 409
    )
    page = _page(client, intel_setup, auth_headers["analyst"])
    assert (
        next(row for row in page["items"] if row["value"] == "evil.net")["suppressed"]
        is True
    )
    identifier = created.json()["id"]
    update = {"expected_version": 1, "reason": "Testing ended.", "active": False}
    changed = client.patch(
        f"{endpoint}/{identifier}", json=update, headers=auth_headers["admin"]
    )
    assert changed.status_code == 200, changed.text
    assert (
        client.patch(
            f"{endpoint}/{identifier}", json=update, headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    history = client.get(
        f"{endpoint}/{identifier}/history", headers=auth_headers["analyst"]
    )
    assert history.status_code == 200 and history.json()["total"] == 2
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == members.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    assert client.get(endpoint, headers=auth_headers["analyst"]).status_code == 404
    assert (
        client.get(
            f"/items/{item.id}/indicators",
            params={"team_id": team["id"]},
            headers=auth_headers["analyst"],
        ).status_code
        == 404
    )


def test_pagination_and_reference_exclusion_are_explicit(
    client, auth_headers, intel_setup
):
    team, item, *_ = intel_setup
    response = client.get(
        f"/items/{item.id}/indicators?page_size=1", headers=auth_headers["viewer"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2 and len(response.json()["items"]) == 1
    assert response.json()["can_review"] is False
    page = _page(client, intel_setup, auth_headers["analyst"])
    example = next(row for row in page["items"] if row["value"] == "example.com")
    assert example["exclusion_reasons"] == ["reserved_example"]
    invalid = client.get(
        f"/items/{item.id}/indicators?page_size=101", headers=auth_headers["analyst"]
    )
    assert invalid.status_code == 422


def test_history_is_removed_with_parent_article(db_session):
    item = _create_item(db_session)
    _extract(db_session, item)
    db_session.delete(item)
    db_session.flush()
    assert db_session.get(ItemIntelState, item.id) is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(ItemIOC).where(ItemIOC.item_id == item.id)
        )
        == 0
    )


def test_source_relabel_preserves_evidence_access_and_original_assessment_history(
    client,
    db_session,
    seed_users,
    auth_headers,
    monkeypatch,
    intel_setup,
):
    from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
    from app.models.feed import Feed
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement

    team, item, *_ = intel_setup
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    feed = db_session.get(Feed, item.feed_id)
    feed.handling_label_id = restricted.id
    item.classification_required_version += 1
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    page = _page(client, intel_setup, auth_headers["admin"])
    indicator = next(row for row in page["items"] if row["value"] == "evil.net")
    endpoint = (
        f"/items/{item.id}/indicators/{indicator['id']}/assessment?team_id={team['id']}"
    )
    saved = client.patch(endpoint, json=_command(page), headers=auth_headers["admin"])
    assert saved.status_code == 200, saved.text
    feed.handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.commit()
    assert (
        client.get(
            f"/items/{item.id}/indicators", headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    # New source evidence can be read, but older restricted analyst notes remain
    # protected by their original captured label and cannot be overwritten.
    item.classification_required_version += 1
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    visible = _page(client, intel_setup, auth_headers["analyst"])
    assert (
        next(row for row in visible["items"] if row["id"] == indicator["id"])[
            "assessment"
        ]
        is None
    )
    assert (
        client.patch(
            endpoint, json=_command(visible), headers=auth_headers["analyst"]
        ).status_code
        == 404
    )
    history = client.get(
        endpoint.replace("?", "/history?"), headers=auth_headers["analyst"]
    )
    assert history.status_code == 200 and history.json()["items"] == []


def test_verdict_events_are_team_scoped_and_reason_only_edits_do_not_reemit(
    client,
    db_session,
    auth_headers,
    intel_setup,
):
    team, item, *_ = intel_setup
    page = _page(client, intel_setup, auth_headers["analyst"])
    indicator = next(row for row in page["items"] if row["value"] == "evil.net")
    endpoint = (
        f"/items/{item.id}/indicators/{indicator['id']}/assessment?team_id={team['id']}"
    )
    first = client.patch(endpoint, json=_command(page), headers=auth_headers["analyst"])
    assert first.status_code == 200, first.text
    events = db_session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.event_type == "intel.indicators.changed"
        )
    ).all()
    team_events = [
        event for event in events if event.payload_json.get("team_id") == team["id"]
    ]
    assert len(team_events) == 1
    from app.services.intel_event_eligibility import automation_event_current

    assert automation_event_current(
        db_session, team_events[0].payload_json, "intel.indicators.changed"
    )
    db_session.commit()
    command = {
        **_command(page, version=1),
        "reason": "Additional explanatory annotation.",
    }
    assert (
        client.patch(
            endpoint, json=command, headers=auth_headers["analyst"]
        ).status_code
        == 200
    )
    events = db_session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.event_type == "intel.indicators.changed"
        )
    ).all()
    assert (
        len(
            [
                event
                for event in events
                if event.payload_json.get("team_id") == team["id"]
            ]
        )
        == 1
    )


def test_full_length_urls_persist_and_can_be_suppressed_without_btree_overflow(
    client,
    db_session,
    auth_headers,
    intel_setup,
):
    import hashlib
    from app.models.ioc import IOC

    team, item, *_ = intel_setup
    # Deterministic high-entropy text resists index TOAST compression. Repeating
    # one character would hide the PostgreSQL btree entry-size limit.
    path = "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(64)
    )[:4000]
    value = "https://evil.net/" + path
    _extract(db_session, item, value)
    db_session.commit()
    stored = db_session.scalar(
        select(IOC).where(IOC.type == "url", IOC.value_norm == value)
    )
    assert stored is not None and stored.value_norm == value
    assert stored.value_digest == hashlib.sha256(value.encode()).hexdigest()
    assert _extract(db_session, item, value) == []
    db_session.commit()
    created = client.post(
        f"/teams/{team['id']}/indicator-suppressions",
        headers=auth_headers["admin"],
        json={
            "ioc_type": "url",
            "value": value,
            "reason": "Approved long test URL.",
            "active": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["value"] == value
