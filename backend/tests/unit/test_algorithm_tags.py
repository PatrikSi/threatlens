import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.feed import Feed
from app.models.item import Item
from app.models.tag import ItemTag, Tag
from app.services.algorithm_tags import build_tag_candidates, sync_item_algorithm_tags


def test_sync_item_algorithm_tags_upserts_and_replaces_algorithm_links(db_session):
    feed = Feed(name="AlgoTag Feed", url="https://example.com/algo-tag.xml", enabled=True, fetch_interval_seconds=1800)
    db_session.add(feed)
    db_session.flush()

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="algo-tag-item",
        url="https://example.com/algo-tag-item",
        canonical_url="https://example.com/algo-tag-item",
        title="Algorithmic Tag Sync",
        summary="summary",
        published_at=datetime.now(timezone.utc),
        dedupe_key="algo-tag-item",
        content_hash="f" * 64,
        status="new",
    )
    db_session.add(item)
    db_session.flush()

    stale = Tag(name="vulnerability")
    db_session.add(stale)
    db_session.flush()
    db_session.add(ItemTag(item_id=item.id, tag_id=stale.id))
    db_session.commit()

    desired = sync_item_algorithm_tags(
        db_session,
        item_id=item.id,
        primary_category="apt_campaign",
        secondary_categories=["supply_chain"],
    )
    db_session.commit()

    assert desired == ["apt_campaign", "supply_chain"]

    linked_names = db_session.execute(
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item.id)
        .order_by(Tag.name.asc())
    ).scalars().all()
    assert linked_names == ["apt_campaign", "supply_chain"]

    links = db_session.execute(
        select(ItemTag)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id == item.id)
        .order_by(Tag.name.asc())
    ).scalars().all()
    assert all(link.source == "rule" for link in links)
    assert all(link.rules_version == "tagging_v2" for link in links)
    assert all(link.confidence >= 0.45 for link in links)


def test_build_tag_candidates_adds_richer_signals():
    candidates = build_tag_candidates(
        primary_category="vulnerability",
        secondary_categories=["apt_campaign"],
        classification_confidence=0.83,
        ioc_values_by_type={
            "cve": ["CVE-2025-12345"],
            "domain": ["malicious.example.com"],
            "vendor": ["microsoft"],
            "program": ["windows server"],
        },
        title="Mustang Panda campaign uses CVE-2025-12345 against Windows Server",
        summary="Threat research from SecureList",
        article_text="Observed APT activity and exploitation against enterprise infrastructure.",
        feed_name="Securelist Threat Research",
        feed_url="https://securelist.com/feed/",
        feedback_adjustments={},
    )
    names = {candidate.name for candidate in candidates}
    assert "vulnerability" in names
    assert "apt_campaign" in names
    assert "ioc:cve" in names
    assert "cve-2025-12345" in names
    assert "vendor:microsoft" in names
    assert "product:windows_server" in names
    assert "campaign:mustang_panda" in names
    assert "source:trusted_research" in names
