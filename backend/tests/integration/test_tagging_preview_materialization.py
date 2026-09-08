import gc
import tracemalloc
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.api.routes.tagging import _build_rule_preview_response
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.schemas.tagging import TaggingRulePreviewRequest
from app.services.bounded_regex import MAX_REGEX_TEXT_CHARS
from app.services.data_access_policy import DataAccessContext


def _feed(db):
    feed = Feed(name="Preview feed", url=f"https://example.com/{uuid.uuid4()}.xml")
    db.add(feed)
    db.flush()
    return feed


def _item(db, feed, *, title="ordinary item", text="ordinary article", newest=False):
    identity = uuid.uuid4()
    item = Item(id=identity, feed_id=feed.id, source_guid=str(identity),
                url=f"https://example.com/{identity}", canonical_url=f"https://example.com/{identity}",
                title=title, dedupe_key=str(identity), content_hash="a" * 64,
                first_seen_at=datetime.now(timezone.utc) + timedelta(days=int(newest)))
    db.add(item)
    db.flush()
    db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text=text))
    return item


def _payload(**changes):
    return {"name": "Test rule", "tag_name": "test", "pattern": "ordinary",
            "match_type": "contains", "applies_to": ["article_text"], **changes}


@pytest.mark.parametrize("condition", ["feed", "category", "confidence"])
def test_ineligible_oversized_newest_item_does_not_abort_preview(
    client, auth_headers, db_session, condition,
):
    selected_feed = _feed(db_session)
    eligible = _item(db_session, selected_feed)
    ineligible = _item(db_session, _feed(db_session), text="x" * (MAX_REGEX_TEXT_CHARS + 1), newest=True)
    for item, category, confidence in [(eligible, "vulnerability", 0.9), (ineligible, "other", 0.1)]:
        db_session.add(ItemClassification(item_id=item.id, primary_category=category,
                                         secondary_categories=[], confidence=confidence, source_hash="test"))
    eligible_id, selected_feed_id = str(eligible.id), str(selected_feed.id)
    db_session.commit()
    changes = {
        "feed": {"feed_scope": "selected", "feed_ids": [selected_feed_id]},
        "category": {"required_categories": ["vulnerability"]},
        "confidence": {"min_classification_confidence": 0.5},
    }[condition]
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"], json=_payload(**changes))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert body["scanned_items"] == body["candidate_items"] == 2
    assert body["warnings"] == []
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [eligible_id]


def test_unselected_oversized_title_is_display_only(client, auth_headers, db_session):
    _item(db_session, _feed(db_session), title="x" * (MAX_REGEX_TEXT_CHARS + 1))
    db_session.commit()
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"], json=_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert body["items"][0]["title"] == "x" * 500 + "…"
    selected = client.post("/tagging/rules/preview", headers=auth_headers["admin"],
                           json=_payload(applies_to=["title"], pattern="x"))
    assert selected.json()["complete"] is False
    assert selected.json()["total"] == 0
    assert "input budget" in selected.json()["warnings"][0]


def test_matching_large_articles_are_not_retained_with_preview_metadata(db_session):
    feed = _feed(db_session)
    for _ in range(25):
        _item(db_session, feed, text="a" * MAX_REGEX_TEXT_CHARS)
    db_session.commit()
    db_session.expunge_all()
    gc.collect()
    context = DataAccessContext(mode="disabled", policy_revision=1, coverage_version=1,
                                principal_type="user", principal_id=uuid.uuid4(),
                                principal_eligible=True, allowed_label_ids=frozenset())
    tracemalloc.start()
    try:
        response = _build_rule_preview_response(
            db_session, TaggingRulePreviewRequest(**_payload(pattern="a", limit=25)), context,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.complete
    assert response.total == len(response.items) == 25
    # The old result list retained about 50 MB of article strings. This allows
    # several transient 2 MB rows/matcher copies, but not accumulating bodies.
    assert peak < 16 * 1024 * 1024, f"preview retained excessive Python allocations: {peak}"
    assert len(response.model_dump_json()) < 20_000


def test_abbreviated_title_still_matches_after_display_prefix(client, auth_headers, db_session):
    _item(db_session, _feed(db_session), title="x" * 600 + " ordinary")
    db_session.commit()
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"],
                           json=_payload(applies_to=["title"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert body["total"] == 1
    assert body["items"][0]["title"] == "x" * 500 + "…"
    assert body["items"][0]["matched_sections"] == ["title"]


def test_current_tag_rows_and_text_are_bounded_in_sql(client, auth_headers, db_session):
    feed = _feed(db_session)
    many = _item(db_session, feed)
    long_name = _item(db_session, feed)
    many_id, long_id = str(many.id), str(long_name.id)
    for index in range(100):
        tag = Tag(name=f"tag-{index:03d}")
        db_session.add(tag)
        db_session.flush()
        db_session.add(ItemTag(item_id=many.id, tag_id=tag.id))
    tag = Tag(name="long-" + "界" * 500)
    db_session.add(tag)
    db_session.flush()
    db_session.add(ItemTag(item_id=long_name.id, tag_id=tag.id))
    db_session.commit()
    statements = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if "row_number() OVER" in statement:
            statements.append((statement, parameters))

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.post("/tagging/rules/preview", headers=auth_headers["admin"], json=_payload())
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True  # Display abbreviation does not hide match failures.
    items = {item["id"]: item for item in body["items"]}
    assert items[many_id]["current_tags"] == [f"tag-{index:03d}" for index in range(25)]
    assert items[long_id]["current_tags"] == ["long-" + "界" * 59 + "…"]
    assert all(item["current_tags_truncated"] for item in items.values())
    assert len(statements) == 1
    statement, parameters = statements[0]
    assert "substr(tags.name" in statement and "position <=" in statement
    assert 65 in parameters.values() and 26 in parameters.values()
