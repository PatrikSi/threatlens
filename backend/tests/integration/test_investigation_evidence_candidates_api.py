from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.security import generate_api_token
from app.core.token_scopes import (
    SCOPE_READ_ITEMS,
    SCOPE_WRITE_INVESTIGATIONS,
)
from app.models.api_token import ApiToken
from app.models.alert_occurrence import AlertOccurrence
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.report import Report


def _search_candidates(
    client: TestClient,
    path: str,
    *,
    params: dict | list[tuple[str, object]] | None = None,
    headers: dict[str, str],
):
    payload: dict[str, object] = {}
    entries = params.items() if isinstance(params, dict) else (params or [])
    for key, value in entries:
        if key == "source_types":
            sources = payload.setdefault("source_types", [])
            assert isinstance(sources, list)
            if isinstance(value, list):
                sources.extend(value)
            else:
                sources.append(value)
        else:
            payload[key] = value
    return client.post(path, json=payload, headers=headers)


def _create_investigation(
    client: TestClient,
    headers: dict[str, str],
    *,
    visibility: str = "private",
) -> dict:
    response = client.post(
        "/investigations",
        headers=headers,
        json={
            "title": f"Candidate search {uuid.uuid4().hex}",
            "description": "Evidence candidate API coverage.",
            "severity": "high",
            "visibility": visibility,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_item(
    db_session,
    *,
    title: str,
    first_seen_at: datetime,
    url: str | None = None,
) -> tuple[Feed, Item]:
    unique = uuid.uuid4().hex
    feed = Feed(
        name=f"Candidate feed {unique[:8]}",
        url=f"https://example.com/feeds/{unique}.xml",
    )
    db_session.add(feed)
    db_session.flush()
    item_url = url or f"https://example.com/articles/{unique}"
    item = Item(
        feed_id=feed.id,
        source_guid=f"candidate-{unique}",
        url=item_url,
        canonical_url=item_url,
        title=title,
        summary=f"Bounded summary for {title}.",
        published_at=first_seen_at - timedelta(hours=1),
        first_seen_at=first_seen_at,
        dedupe_key=f"candidate:{unique}",
        content_hash=(unique * 2)[:64],
        status="content_fetched",
    )
    db_session.add(item)
    db_session.commit()
    return feed, item


def _create_report(db_session, *, owner, title: str, observed_at: datetime) -> Report:
    report = Report(
        owner_user_id=owner.id,
        title=title,
        report_type="threat_brief",
        status="ready",
        trigger_source="manual",
        generation_stage="completed",
        period_start=observed_at - timedelta(days=1),
        period_end=observed_at,
        summary_text=f"Summary for {title}.",
        generated_at=observed_at,
        created_at=observed_at,
    )
    db_session.add(report)
    db_session.commit()
    return report


def _create_alert(
    db_session,
    *,
    owner,
    item: Item,
    name: str,
    observed_at: datetime,
) -> AlertOccurrence:
    occurrence = AlertOccurrence(
        rule_id_snapshot=uuid.uuid4(),
        owner_user_id=owner.id,
        item_id=item.id,
        item_id_snapshot=item.id,
        rule_revision=1,
        item_content_hash=item.content_hash,
        alert_name_snapshot=name,
        alert_category_snapshot="infrastructure",
        alert_keywords_snapshot=["beacon"],
        matched_keywords=["beacon"],
        source_snapshot_json={
            "item": {
                "id": str(item.id),
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "canonical_url": item.canonical_url,
            }
        },
        severity_snapshot="high",
        lifecycle_state="new",
        created_at=observed_at,
    )
    db_session.add(occurrence)
    db_session.commit()
    return occurrence


def _create_api_token(db_session, user, *, scopes: list[str]) -> str:
    token_value, prefix, token_hash = generate_api_token()
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"candidate-{uuid.uuid4().hex[:8]}",
            token_prefix=prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.commit()
    return token_value


def test_blank_candidate_search_returns_recent_sources_with_bounded_metadata(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["admin"])
    feed, item = _create_item(
        db_session,
        title="Recent candidate article",
        first_seen_at=now - timedelta(hours=1),
    )
    ioc = IOC(
        type="domain",
        value_raw="recent-candidate.example",
        value_norm="recent-candidate.example",
    )
    db_session.add(ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=item.id, ioc_id=ioc.id, occurrences=2))
    report = _create_report(
        db_session,
        owner=seed_users["admin"],
        title="Recent candidate report",
        observed_at=now - timedelta(hours=2),
    )
    alert = _create_alert(
        db_session,
        owner=seed_users["admin"],
        item=item,
        name="Recent candidate alert",
        observed_at=now - timedelta(minutes=30),
    )
    db_session.commit()

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"range": "7d", "page_size": 20},
        headers=auth_headers["admin"],
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["query_analysis"] == {
        "kind": "empty",
        "normalized_value": None,
        "detected_ioc_type": None,
    }
    assert payload["total_truncated"] is False
    candidates = {
        (candidate["source_type"], candidate["source_id"]): candidate
        for candidate in payload["candidates"]
    }
    assert ("item", str(item.id)) in candidates
    assert ("ioc", str(ioc.id)) in candidates
    assert ("report", str(report.id)) in candidates
    assert ("alert_occurrence", str(alert.id)) in candidates
    assert candidates[("item", str(item.id))]["source_id"] == str(item.id)
    assert candidates[("item", str(item.id))]["source_label"] == feed.name
    assert "text" not in candidates[("item", str(item.id))]["metadata"]
    assert candidates[("ioc", str(ioc.id))]["metadata"]["related_items"] == []
    assert "content_hash" not in candidates[("item", str(item.id))]["metadata"]

    anchored_page = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={
            "range": "7d",
            "page": 2,
            "page_size": 1,
            "as_of": payload["effective_until"],
        },
        headers=auth_headers["admin"],
    )
    assert anchored_page.status_code == 200, anchored_page.text
    assert anchored_page.json()["effective_until"] == payload["effective_until"]

    detail = client.get(
        f"/investigations/{investigation['id']}",
        headers=auth_headers["admin"],
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["version"] == investigation["version"]
    assert detail.json()["evidence_count"] == 0
    assert detail.json()["evidence"] == []


def test_candidate_search_ranks_exact_ioc_then_related_item_and_marks_attachment(
    client: TestClient,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _feed, item = _create_item(
        db_session,
        title="Article about exact-target.example",
        first_seen_at=now - timedelta(hours=1),
    )
    ioc = IOC(
        type="domain",
        value_raw="exact-target.example",
        value_norm="exact-target.example",
    )
    db_session.add(ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=item.id, ioc_id=ioc.id))
    additional_items: list[Item] = []
    for index in range(4):
        _related_feed, related_item = _create_item(
            db_session,
            title=f"Related candidate {index}",
            first_seen_at=now - timedelta(minutes=10 + index),
        )
        additional_items.append(related_item)
        db_session.add(ItemIOC(item_id=related_item.id, ioc_id=ioc.id))
    db_session.commit()
    attached = client.post(
        f"/investigations/{investigation['id']}/evidence",
        headers=auth_headers["analyst"],
        json={
            "source_type": "item",
            "source_id": str(item.id),
            "expected_version": investigation["version"],
        },
    )
    assert attached.status_code == 200, attached.text

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params=[
            ("q", "EXACT-TARGET.EXAMPLE"),
            ("source_types", "item"),
            ("source_types", "ioc"),
            ("range", "7d"),
        ],
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["query_analysis"] == {
        "kind": "ioc",
        "normalized_value": "exact-target.example",
        "detected_ioc_type": "domain",
    }
    assert [entry["source_type"] for entry in payload["candidates"][:2]] == [
        "ioc",
        "item",
    ]
    assert payload["candidates"][0]["match_reason"] == "exact_ioc"
    assert payload["candidates"][0]["metadata"]["observation_count"] == 5
    assert len(payload["candidates"][0]["metadata"]["related_items"]) == 3
    assert [
        related["item_id"]
        for related in payload["candidates"][0]["metadata"]["related_items"]
    ] == [str(item.id) for item in additional_items[:3]]
    item_candidate = next(
        entry
        for entry in payload["candidates"]
        if entry["source_type"] == "item" and entry["source_id"] == str(item.id)
    )
    assert item_candidate["match_reason"] == "related_ioc"
    assert item_candidate["already_attached"] is True


def test_candidate_search_normalizes_and_matches_ipv6_iocs(
    client: TestClient,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _feed, item = _create_item(
        db_session,
        title="IPv6 infrastructure candidate",
        first_seen_at=now - timedelta(hours=1),
    )
    ioc = IOC(
        type="ipv6",
        value_raw="2001:db8::7",
        value_norm="2001:db8::7",
    )
    db_session.add(ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=item.id, ioc_id=ioc.id))
    db_session.commit()

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={
            "q": "2001:0DB8:0:0:0:0:0:7",
            "source_types": "ioc",
            "range": "7d",
        },
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["query_analysis"] == {
        "kind": "ioc",
        "normalized_value": "2001:db8::7",
        "detected_ioc_type": "ipv6",
    }
    assert payload["candidates"][0]["source_id"] == str(ioc.id)
    assert payload["candidates"][0]["source_label"] == "IPv6"
    assert payload["candidates"][0]["match_reason"] == "exact_ioc"


def test_candidate_search_uses_item_ids_exact_urls_ranges_and_pagination(
    client: TestClient,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    exact_url = "https://intel.example.test/campaign?id=42"
    pasted_url = (
        "HTTPS://intel.example.test:443/campaign/?utm_source=mail&id=42#section"
    )
    _feed, recent = _create_item(
        db_session,
        title="Exact URL candidate",
        first_seen_at=now - timedelta(hours=1),
        url=exact_url,
    )
    _old_feed, old = _create_item(
        db_session,
        title="Older candidate",
        first_seen_at=now - timedelta(days=14),
    )
    _host_feed, host_item = _create_item(
        db_session,
        title="Newer hostname observation",
        first_seen_at=now - timedelta(minutes=5),
    )
    host_ioc = IOC(
        type="domain",
        value_raw="intel.example.test",
        value_norm="intel.example.test",
    )
    db_session.add(host_ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=host_item.id, ioc_id=host_ioc.id, occurrences=1))
    db_session.commit()

    exact = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params=[
            ("q", pasted_url),
            ("source_types", "item"),
            ("source_types", "ioc"),
            ("range", "7d"),
        ],
        headers=auth_headers["analyst"],
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["candidates"][0]["source_id"] == str(recent.id)
    assert exact.json()["candidates"][0]["match_reason"] == "exact_url"
    assert any(
        candidate["source_id"] == str(host_ioc.id)
        for candidate in exact.json()["candidates"][1:]
    )

    short_window = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"source_types": "item", "range": "7d", "page_size": 50},
        headers=auth_headers["analyst"],
    )
    assert str(old.id) not in {
        entry["source_id"] for entry in short_window.json()["candidates"]
    }
    long_window = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"source_types": "item", "range": "30d", "page_size": 1, "page": 3},
        headers=auth_headers["analyst"],
    )
    assert long_window.status_code == 200, long_window.text
    assert long_window.json()["total"] == 3
    assert long_window.json()["page"] == 3
    assert long_window.json()["candidates"][0]["source_id"] == str(old.id)


def test_exact_uuid_search_finds_authorized_sources_outside_the_time_window(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
):
    observed_at = datetime.now(timezone.utc) - timedelta(days=120)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _feed, item = _create_item(
        db_session,
        title="Historical exact-ID article",
        first_seen_at=observed_at,
    )
    ioc = IOC(
        type="domain",
        value_raw="historical-exact-id.example",
        value_norm="historical-exact-id.example",
    )
    db_session.add(ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=item.id, ioc_id=ioc.id))
    report = _create_report(
        db_session,
        owner=seed_users["analyst"],
        title="Historical exact-ID report",
        observed_at=observed_at,
    )
    alert = _create_alert(
        db_session,
        owner=seed_users["analyst"],
        item=item,
        name="Historical exact-ID alert",
        observed_at=observed_at,
    )
    _decoy_feed, _decoy = _create_item(
        db_session,
        title=f"Article mentioning {item.id}",
        first_seen_at=observed_at,
    )
    db_session.commit()

    sources = (
        ("item", item.id),
        ("ioc", ioc.id),
        ("report", report.id),
        ("alert_occurrence", alert.id),
    )
    for source_type, source_id in sources:
        response = _search_candidates(
            client,
            f"/investigations/{investigation['id']}/evidence-candidates",
            params={
                "q": str(source_id),
                "source_types": source_type,
                "range": "24h",
            },
            headers=auth_headers["analyst"],
        )

        assert response.status_code == 200, (source_type, response.text)
        payload = response.json()
        assert payload["query_analysis"]["kind"] == "uuid"
        assert payload["total"] == 1
        assert [
            (candidate["source_type"], candidate["source_id"])
            for candidate in payload["candidates"]
        ] == [(source_type, str(source_id))]
        assert payload["candidates"][0]["match_reason"] == "exact_id"

    ioc_response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"q": str(ioc.id), "source_types": "ioc", "range": "24h"},
        headers=auth_headers["analyst"],
    )
    assert ioc_response.json()["candidates"][0]["metadata"]["related_items"][0][
        "item_id"
    ] == str(item.id)


def test_candidate_search_ranks_exact_titles_before_newer_prefix_matches(
    client: TestClient,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _exact_feed, exact = _create_item(
        db_session,
        title="Precise candidate",
        first_seen_at=now - timedelta(hours=2),
    )
    _prefix_feed, prefix = _create_item(
        db_session,
        title="Precise candidate follow-up",
        first_seen_at=now - timedelta(minutes=10),
    )

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"q": "Precise candidate", "source_types": "item"},
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    assert [entry["source_id"] for entry in response.json()["candidates"][:2]] == [
        str(exact.id),
        str(prefix.id),
    ]
    assert response.json()["candidates"][0]["match_reason"] == "exact_text"
    assert response.json()["candidates"][1]["match_reason"] == "prefix"


def test_candidate_pagination_is_stable_across_source_boundaries(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["admin"])
    _recent_feed, recent_item = _create_item(
        db_session,
        title="Recent boundary article",
        first_seen_at=now - timedelta(minutes=5),
    )
    recent_report = _create_report(
        db_session,
        owner=seed_users["admin"],
        title="Recent boundary report",
        observed_at=now - timedelta(minutes=5),
    )
    _older_feed, older_item = _create_item(
        db_session,
        title="Older boundary article",
        first_seen_at=now - timedelta(minutes=10),
    )
    older_report = _create_report(
        db_session,
        owner=seed_users["admin"],
        title="Older boundary report",
        observed_at=now - timedelta(minutes=10),
    )
    db_session.commit()

    path = f"/investigations/{investigation['id']}/evidence-candidates"
    first_page = _search_candidates(
        client,
        path,
        params=[
            ("source_types", "item"),
            ("source_types", "report"),
            ("range", "7d"),
            ("page_size", 2),
        ],
        headers=auth_headers["admin"],
    )
    assert first_page.status_code == 200, first_page.text
    anchor = first_page.json()["effective_until"]
    second_page = _search_candidates(
        client,
        path,
        params=[
            ("source_types", "item"),
            ("source_types", "report"),
            ("range", "7d"),
            ("page_size", 2),
            ("page", 2),
            ("as_of", anchor),
        ],
        headers=auth_headers["admin"],
    )
    assert second_page.status_code == 200, second_page.text

    observed = [
        (candidate["source_type"], candidate["source_id"])
        for payload in (first_page.json(), second_page.json())
        for candidate in payload["candidates"]
    ]
    assert observed == [
        ("item", str(recent_item.id)),
        ("report", str(recent_report.id)),
        ("item", str(older_item.id)),
        ("report", str(older_report.id)),
    ]
    assert len(observed) == len(set(observed))


def test_candidate_search_treats_like_wildcards_as_literal_text(
    client: TestClient,
    auth_headers,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _matching_feed, matching = _create_item(
        db_session,
        title="Candidate coverage reached 100%",
        first_seen_at=now,
    )
    _plain_feed, plain = _create_item(
        db_session,
        title="Candidate coverage reached 100 percent",
        first_seen_at=now,
    )

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"q": "100%", "source_types": "item"},
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    assert [entry["source_id"] for entry in response.json()["candidates"]] == [
        str(matching.id)
    ]
    assert str(plain.id) not in {
        entry["source_id"] for entry in response.json()["candidates"]
    }


def test_candidate_permissions_are_capability_locked_without_source_enumeration(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
):
    investigation = _create_investigation(client, auth_headers["analyst"])
    _feed, _item = _create_item(
        db_session,
        title="Must remain locked",
        first_seen_at=datetime.now(timezone.utc),
    )
    token = _create_api_token(
        db_session,
        seed_users["analyst"],
        scopes=[SCOPE_WRITE_INVESTIGATIONS],
    )

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params=[("source_types", "item"), ("source_types", "ioc")],
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["candidates"] == []
    assert response.json()["total"] == 0
    capabilities = {
        entry["source_type"]: entry for entry in response.json()["source_capabilities"]
    }
    assert capabilities["item"] == {
        "source_type": "item",
        "available": False,
        "unavailable_reason": "Requires read:items.",
        "required_permissions": ["read:items"],
    }

    allowed_token = _create_api_token(
        db_session,
        seed_users["analyst"],
        scopes=[SCOPE_WRITE_INVESTIGATIONS, SCOPE_READ_ITEMS],
    )
    allowed = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params={"source_types": "item"},
        headers={"Authorization": f"Bearer {allowed_token}"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] == 1


def test_report_and_alert_candidates_enforce_current_user_ownership(
    client: TestClient,
    auth_headers,
    seed_users,
    db_session,
):
    now = datetime.now(timezone.utc)
    investigation = _create_investigation(client, auth_headers["analyst"])
    _feed, item = _create_item(
        db_session,
        title="Ownership candidate article",
        first_seen_at=now - timedelta(hours=1),
    )
    own_report = _create_report(
        db_session,
        owner=seed_users["analyst"],
        title="Owned candidate report",
        observed_at=now - timedelta(hours=2),
    )
    other_report = _create_report(
        db_session,
        owner=seed_users["admin"],
        title="Foreign candidate report",
        observed_at=now - timedelta(hours=2),
    )
    own_alert = _create_alert(
        db_session,
        owner=seed_users["analyst"],
        item=item,
        name="Owned candidate alert",
        observed_at=now - timedelta(minutes=30),
    )
    other_alert = _create_alert(
        db_session,
        owner=seed_users["admin"],
        item=item,
        name="Foreign candidate alert",
        observed_at=now - timedelta(minutes=30),
    )

    response = _search_candidates(
        client,
        f"/investigations/{investigation['id']}/evidence-candidates",
        params=[
            ("source_types", "report"),
            ("source_types", "alert_occurrence"),
        ],
        headers=auth_headers["analyst"],
    )

    assert response.status_code == 200, response.text
    identities = {
        (candidate["source_type"], candidate["source_id"])
        for candidate in response.json()["candidates"]
    }
    assert ("report", str(own_report.id)) in identities
    assert ("report", str(other_report.id)) not in identities
    assert ("alert_occurrence", str(own_alert.id)) in identities
    assert ("alert_occurrence", str(other_alert.id)) not in identities


def test_candidate_search_enforces_object_access_archive_and_request_bounds(
    client: TestClient,
    auth_headers,
    seed_users,
):
    private = _create_investigation(client, auth_headers["analyst"])
    hidden = _search_candidates(
        client,
        f"/investigations/{private['id']}/evidence-candidates",
        headers=auth_headers["admin"],
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "investigation_not_found"

    member = client.post(
        f"/investigations/{private['id']}/members",
        headers=auth_headers["analyst"],
        json={
            "user_id": str(seed_users["admin"].id),
            "role": "viewer",
            "expected_version": private["version"],
        },
    )
    assert member.status_code == 200, member.text
    read_only_member = _search_candidates(
        client,
        f"/investigations/{private['id']}/evidence-candidates",
        headers=auth_headers["admin"],
    )
    assert read_only_member.status_code == 403
    assert "membership is read-only" in read_only_member.json()["detail"]

    viewer_denied = _search_candidates(
        client,
        f"/investigations/{private['id']}/evidence-candidates",
        headers=auth_headers["viewer"],
    )
    assert viewer_denied.status_code == 403

    archived = client.patch(
        f"/investigations/{private['id']}",
        headers=auth_headers["analyst"],
        json={"expected_version": member.json()["version"], "status": "archived"},
    )
    assert archived.status_code == 200, archived.text
    archived_search = _search_candidates(
        client,
        f"/investigations/{private['id']}/evidence-candidates",
        headers=auth_headers["analyst"],
    )
    assert archived_search.status_code == 409
    assert archived_search.json()["error"]["code"] == "investigation_archived"

    active = _create_investigation(client, auth_headers["analyst"])
    for params in (
        {"range": "365d"},
        {"page": 21},
        {"page_size": 51},
        {"q": "x" * 256},
        {"q": "signal\nvalue"},
    ):
        rejected = _search_candidates(
            client,
            f"/investigations/{active['id']}/evidence-candidates",
            params=params,
            headers=auth_headers["analyst"],
        )
        assert rejected.status_code == 422, (params, rejected.text)

    short_fuzzy = _search_candidates(
        client,
        f"/investigations/{active['id']}/evidence-candidates",
        params={"q": "ab"},
        headers=auth_headers["analyst"],
    )
    assert short_fuzzy.status_code == 422, short_fuzzy.text
    assert short_fuzzy.json()["detail"] == (
        "Text evidence searches must contain at least 3 characters."
    )
    assert short_fuzzy.json()["error"]["code"] == "validation_error"
    assert short_fuzzy.headers["x-error-code"] == "validation_error"
