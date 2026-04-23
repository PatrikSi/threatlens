import uuid

from app.models.feed import Feed
from app.services.feed_storage import decrypt_feed_url, feed_url_digest


def test_feed_model_encrypts_url_storage_and_exposes_plaintext():
    plaintext_url = "https://alice:secret@example.com/path/feed.xml?token=alpha"

    feed = Feed(
        id=uuid.uuid4(),
        name="Credential Feed",
        url=plaintext_url,
        enabled=True,
        fetch_interval_seconds=1800,
    )

    assert feed.url == plaintext_url
    assert feed._url_encrypted != plaintext_url
    assert feed._url_encrypted.startswith("enc:v1:")
    assert decrypt_feed_url(feed._url_encrypted) == plaintext_url
    assert feed.url_digest == feed_url_digest(plaintext_url)


def test_feed_url_digest_keeps_authenticated_variants_distinct():
    token_a = feed_url_digest("https://example.com/path/feed.xml?token=alpha")
    token_b = feed_url_digest("https://example.com/path/feed.xml?token=beta")
    user_a = feed_url_digest("https://alice:secret@example.com/path/feed.xml")
    user_b = feed_url_digest("https://bob:secret@example.com/path/feed.xml")

    assert token_a != token_b
    assert user_a != user_b
