import hashlib
import uuid

from app.models.feed import Feed
from app.services.feed_storage import UNREADABLE_FEED_URL_ERROR, decrypt_feed_url, feed_url_digest


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


def test_feed_url_digest_is_not_plain_sha256_of_url():
    url = "https://alice:secret@example.com/path/feed.xml?token=alpha"

    assert feed_url_digest(url) != hashlib.sha256(url.encode("utf-8")).hexdigest()


def test_feed_model_exposes_blank_url_and_error_when_ciphertext_is_unreadable():
    feed = Feed(
        id=uuid.uuid4(),
        name="Broken feed",
        url="https://example.com/feed.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
    feed._url_encrypted = "enc:v1:not-a-valid-fernet-token"

    assert feed.url == ""
    assert feed.url_decryption_error == UNREADABLE_FEED_URL_ERROR
