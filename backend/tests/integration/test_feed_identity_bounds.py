"""Publisher identities remain exact without exceeding PostgreSQL index limits."""

import hashlib
import random
import string
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.feed import Feed
from app.models.item import Item
from app.services.connectors.rss import RSSConnector
from app.services.feed_pipeline import upsert_item_from_parsed


def _entries(guid: str, url: str):
    body = f'''<rss version="2.0"><channel><title>Identity regression</title>
      <item><guid>normal-id</guid><title>Normal entry</title><link>https://source.example/normal</link></item>
      <item><guid>{guid}</guid><title>Long identity</title><link>{url}</link></item>
    </channel></rss>'''.encode()
    return RSSConnector().poll({"body": body}, None)[0]


@pytest.mark.parametrize("prefix", ["", "資料"])
def test_large_identifiers_do_not_rollback_other_entries(db_session, prefix):
    guid = prefix + "".join(random.Random(3).choices(string.ascii_letters + string.digits, k=8192))
    url = "https://source.example/" + guid
    entries = _entries(guid, url)
    feed = Feed(name="Long identifiers", url=f"https://source.example/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    inserted = [upsert_item_from_parsed(db_session, feed, entry)[0] for entry in entries]
    inserted[1].canonical_url = url
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(Item).where(Item.feed_id == feed.id)) == 2
    assert inserted[1].source_guid == guid
    assert inserted[1].source_guid_digest == hashlib.sha256(guid.encode()).hexdigest()
    assert inserted[1].dedupe_digest == hashlib.sha256(inserted[1].dedupe_key.encode()).hexdigest()
    same, changed, created = upsert_item_from_parsed(db_session, feed, entries[1])
    assert same.id == inserted[1].id and not changed and not created
    # Distinct long identities cannot collapse merely because prefixes match.
    another = _entries(guid + "z", url)[1]
    assert upsert_item_from_parsed(db_session, feed, another)[2]
    # Feed-scoped GUID uniqueness remains enforced for every writer, even with
    # an unrelated dedupe key; the database computes both digests itself.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Item(feed_id=feed.id, source_guid=guid, url=url, title="Duplicate",
                            dedupe_key=str(uuid.uuid4()), content_hash="a" * 64))
        db_session.flush()


def test_long_url_fallback_preserves_deduplication(db_session):
    url = "https://source.example/" + "".join(random.Random(7).choices(string.ascii_letters, k=8192))
    entry = _entries("", url)[1]
    feed = Feed(name="Long URL", url=f"https://source.example/{uuid.uuid4()}")
    db_session.add(feed)
    db_session.flush()
    original, _, created = upsert_item_from_parsed(db_session, feed, entry)
    assert created
    same, changed, created = upsert_item_from_parsed(db_session, feed, entry)
    assert same.id == original.id and not changed and not created
