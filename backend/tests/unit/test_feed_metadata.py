from types import SimpleNamespace

from app.services.feed_metadata import apply_probe_metadata, needs_metadata_backfill


def test_needs_metadata_backfill_detects_placeholder_feed_names():
    feed = SimpleNamespace(
        name="https://example.com/feed.xml",
        url="https://example.com/feed.xml",
        description=None,
        site_url=None,
        language=None,
        etag=None,
        last_modified=None,
    )

    assert needs_metadata_backfill(feed) is True


def test_apply_probe_metadata_only_fills_missing_values():
    feed = SimpleNamespace(
        name="https://example.com/feed.xml",
        url="https://example.com/feed.xml",
        description=None,
        site_url=None,
        language="en-us",
        etag=None,
        last_modified=None,
    )
    metadata = SimpleNamespace(
        name="Example Feed",
        description="Threat intel updates",
        site_url="https://example.com",
        language="fr-fr",
        etag="etag-value",
        last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
    )

    changed = apply_probe_metadata(feed, metadata)

    assert changed is True
    assert feed.name == "Example Feed"
    assert feed.description == "Threat intel updates"
    assert feed.site_url == "https://example.com"
    assert feed.language == "en-us"
    assert feed.etag == "etag-value"
    assert feed.last_modified == "Mon, 01 Jan 2026 00:00:00 GMT"
