"""SOC event selection exposes bounded, authorized snapshot context."""

import uuid

from app.models.feed import Feed
from app.models.item import Item
from app.services.integration_events import emit_integration_event


def _article_event(db, *, title: str, feed_name: str):
    identity = uuid.uuid4()
    feed = Feed(name=feed_name, url=f"https://source.example/{identity}")
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title=title,
        url=f"https://source.example/articles/{identity}",
        dedupe_key=str(identity),
        content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    event = emit_integration_event(
        db,
        event_type="rss_item_new",
        source_type="item",
        source_id=item.id,
        idempotency_key=f"picker:{identity}",
        payload={"item_id": str(item.id), "feed_id": str(feed.id)},
    )
    db.commit()
    return event


def test_event_picker_uses_captured_article_and_feed_names(
    client, db_session, auth_headers
):
    event = _article_event(
        db_session, title="Credential theft\nfrom exposed devices", feed_name="SOC feed"
    )
    response = client.get(
        "/notifications/webhooks/events?event_type=rss_item_new",
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 200, response.text
    selected = next(row for row in response.json()["events"] if row["id"] == str(event.id))
    assert "Credential theft from exposed devices" in selected["label"]
    assert "SOC feed" in selected["label"]
    assert "rss_item_new" in selected["label"]
    assert selected["created_at"]
    assert "payload_json" not in selected


def test_event_picker_keeps_long_titles_bounded(client, db_session, auth_headers):
    event = _article_event(db_session, title="A" * 1000, feed_name="B" * 200)
    response = client.get(
        "/notifications/webhooks/events?event_type=rss_item_new&limit=1",
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 200, response.text
    rows = response.json()["events"]
    assert len(rows) == 1
    assert rows[0]["id"] == str(event.id)
    assert "A" * 160 in rows[0]["label"]
    assert "A" * 161 not in rows[0]["label"]
    assert "B" * 80 in rows[0]["label"]
    assert len(rows[0]["label"]) < 320
