from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.data_policy import (
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_state import ItemState
from app.services import data_access_policy


def _feed(
    name: str,
    url: str,
    *,
    handling_label_id: uuid.UUID,
) -> Feed:
    feed = Feed(name=name, handling_label_id=handling_label_id)
    feed.url = url
    return feed


def _item(
    feed: Feed,
    *,
    title: str,
    seen_at: datetime,
) -> Item:
    return Item(
        feed_id=feed.id,
        url=f"https://example.com/articles/{uuid.uuid4()}",
        title=title,
        dedupe_key=f"policy-coverage:{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex * 2,
        first_seen_at=seen_at,
        status="content_fetched",
    )


def _enable_enforcement(
    db_session,
    seed_users,
    monkeypatch,
) -> HandlingLabel:
    restricted = HandlingLabel(
        key=f"restricted-{uuid.uuid4().hex[:12]}",
        name="Restricted coverage",
        description="Coverage test label.",
        color="#B91C1C",
        is_unrestricted=False,
        is_system=False,
        is_active=True,
        revision=1,
        created_by_user_id=seed_users["admin"].id,
        updated_by_user_id=seed_users["admin"].id,
    )
    db_session.add(restricted)
    db_session.flush()
    db_session.add(
        DataPolicyRoleGrant(
            label_id=restricted.id,
            role_id=SYSTEM_ROLE_IDS["admin"],
            granted_by_user_id=seed_users["admin"].id,
        )
    )
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = "enforced"
    state.coverage_version = 1
    state.revision += 1
    state.enforced_at = datetime.now(timezone.utc)
    state.enforced_by_user_id = seed_users["admin"].id
    state.updated_by_user_id = seed_users["admin"].id
    db_session.add(state)
    db_session.commit()
    monkeypatch.setattr(
        data_access_policy,
        "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
        1,
    )
    return restricted


def test_feed_lists_exports_objects_and_url_conflicts_enforce_labels(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch,
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    visible_feed = _feed(
        "Visible policy feed",
        "https://example.com/visible-policy.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = _feed(
        "Restricted policy feed",
        "https://example.com/restricted-policy.xml",
        handling_label_id=restricted.id,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.commit()

    listed = client.get("/feeds", headers=auth_headers["analyst"])
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()} == {str(visible_feed.id)}

    exported = client.get("/feeds/export", headers=auth_headers["analyst"])
    assert exported.status_code == 200, exported.text
    assert [row["name"] for row in exported.json()["feeds"]] == [visible_feed.name]

    admin_backup = client.get(
        "/feeds/export/backup",
        headers=auth_headers["admin"],
    )
    assert admin_backup.status_code == 200, admin_backup.text
    assert {row["name"] for row in admin_backup.json()["feeds"]} == {
        visible_feed.name,
        restricted_feed.name,
    }

    hidden_refresh = client.post(
        f"/feeds/{restricted_feed.id}/refresh",
        headers=auth_headers["analyst"],
    )
    assert hidden_refresh.status_code == 404
    assert hidden_refresh.json()["detail"] == "Feed not found"

    hidden_update = client.patch(
        f"/feeds/{restricted_feed.id}",
        headers=auth_headers["analyst"],
        json={"name": "Should not change"},
    )
    assert hidden_update.status_code == 404
    db_session.refresh(restricted_feed)
    assert restricted_feed.name == "Restricted policy feed"

    duplicate = client.post(
        "/feeds",
        headers=auth_headers["analyst"],
        json={
            "name": "Unknown collision",
            "url": restricted_feed.url,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Feed could not be created with this URL."
    assert "exist" not in duplicate.text.lower()


def test_item_reads_mutations_preview_and_graph_enforce_feed_labels(
    client,
    auth_headers,
    seed_users,
    db_session,
    monkeypatch,
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    visible_feed = _feed(
        "Visible item feed",
        "https://example.com/visible-items.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = _feed(
        "Restricted item feed",
        "https://example.com/restricted-items.xml",
        handling_label_id=restricted.id,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    visible_seen_at = datetime.now(timezone.utc) - timedelta(days=2)
    restricted_seen_at = datetime.now(timezone.utc) - timedelta(days=1)
    visible_item = _item(
        visible_feed,
        title="Visible policy item",
        seen_at=visible_seen_at,
    )
    restricted_item = _item(
        restricted_feed,
        title="Restricted policy item",
        seen_at=restricted_seen_at,
    )
    db_session.add_all([visible_item, restricted_item])
    db_session.flush()
    shared_ioc = IOC(
        type="domain",
        value_raw="shared.example",
        value_norm="shared.example",
        first_seen_at=visible_seen_at,
        last_seen_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(shared_ioc)
    db_session.flush()
    db_session.add_all(
        [
            ItemIOC(item_id=visible_item.id, ioc_id=shared_ioc.id),
            ItemIOC(item_id=restricted_item.id, ioc_id=shared_ioc.id),
        ]
    )
    db_session.commit()

    listed = client.get("/items?page_size=50", headers=auth_headers["analyst"])
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert [row["id"] for row in listed.json()["items"]] == [str(visible_item.id)]

    random_id = uuid.uuid4()
    hidden_detail = client.get(
        f"/items/{restricted_item.id}",
        headers=auth_headers["analyst"],
    )
    missing_detail = client.get(
        f"/items/{random_id}",
        headers=auth_headers["analyst"],
    )
    assert hidden_detail.status_code == missing_detail.status_code == 404
    assert hidden_detail.json()["detail"] == missing_detail.json()["detail"]
    assert (
        hidden_detail.json()["error"]["code"] == missing_detail.json()["error"]["code"]
    )

    preview_fetches = 0

    def _unexpected_preview(*_args, **_kwargs):
        nonlocal preview_fetches
        preview_fetches += 1
        raise AssertionError("restricted preview must not initiate article retrieval")

    monkeypatch.setattr(
        "app.api.routes.items.fetch_article_preview_document",
        _unexpected_preview,
    )
    hidden_preview = client.get(
        f"/items/{restricted_item.id}/article-preview",
        headers=auth_headers["analyst"],
    )
    assert hidden_preview.status_code == 404
    assert preview_fetches == 0

    hidden_mutation = client.post(
        f"/items/{restricted_item.id}/read",
        headers=auth_headers["analyst"],
        json={"is_read": True},
    )
    assert hidden_mutation.status_code == 404
    assert (
        db_session.scalar(
            select(ItemState).where(
                ItemState.user_id == seed_users["analyst"].id,
                ItemState.item_id == restricted_item.id,
            )
        )
        is None
    )

    graph = client.get(
        f"/items/{visible_item.id}/graph?related_item_limit=20",
        headers=auth_headers["analyst"],
    )
    assert graph.status_code == 200, graph.text
    nodes = {node["id"]: node for node in graph.json()["nodes"]}
    assert f"item:{visible_item.id}" in nodes
    assert f"item:{restricted_item.id}" not in nodes
    assert nodes[f"ioc:{shared_ioc.id}"]["metadata"]["last_seen_at"] == (
        visible_seen_at.isoformat()
    )

    hidden_graph = client.get(
        f"/items/{restricted_item.id}/graph",
        headers=auth_headers["analyst"],
    )
    assert hidden_graph.status_code == 404
