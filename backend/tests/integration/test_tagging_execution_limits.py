import uuid

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.services.bounded_regex import MAX_REGEX_TEXT_CHARS


def _seed_items(db, count=1, *, title="ordinary item", article=None):
    feed = Feed(name="Bounded preview", url="https://example.com/preview.xml")
    db.add(feed)
    db.flush()
    for index in range(count):
        identity = uuid.uuid4()
        item = Item(id=identity, feed_id=feed.id, source_guid=str(identity),
                    url=f"https://example.com/{identity}", canonical_url=f"https://example.com/{identity}",
                    title=title, dedupe_key=str(identity), content_hash="a" * 64)
        db.add(item)
        db.flush()
        if article is not None:
            db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text=article))
    db.commit()


def _payload(**changes):
    return {"name": "Test rule", "tag_name": "test", "pattern": "ordinary",
            "match_type": "contains", "applies_to": ["title"], **changes}


def test_regex_preview_reports_timeout_instead_of_a_complete_no_match(client, auth_headers, db_session):
    _seed_items(db_session, title="a" * 20_000 + "!")
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"],
                           json=_payload(match_type="regex", pattern="(a+)+$"))
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is False
    assert body["scanned_items"] == body["candidate_items"] == 1
    assert body["total"] == 0
    assert "50 ms" in body["warnings"][0]


def test_regex_create_validates_in_the_isolated_compiler(client, auth_headers):
    response = client.post("/tagging/rules", headers=auth_headers["admin"],
                           json=_payload(match_type="regex", pattern="["))
    assert response.status_code == 422
    assert "regular expression is invalid" in str(response.json())


def test_preview_discloses_corpus_scan_limit(client, auth_headers, db_session):
    _seed_items(db_session, 205)
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"], json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is False
    assert body["scanned_items"] == body["total"] == 200
    assert body["candidate_items"] == 205
    assert len(body["items"]) == 5


def test_preview_does_not_load_unused_large_article_or_silently_match_truncation(client, auth_headers, db_session):
    _seed_items(db_session, article="x" * (MAX_REGEX_TEXT_CHARS + 500_000))
    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"], json=_payload())
    assert response.status_code == 200
    assert response.json()["complete"] is True
    assert response.json()["total"] == 1

    response = client.post("/tagging/rules/preview", headers=auth_headers["admin"],
                           json=_payload(pattern="x", applies_to=["article_text"]))
    assert response.status_code == 200
    assert response.json()["complete"] is False
    assert response.json()["total"] == 0
    assert "input budget" in response.json()["warnings"][0]
