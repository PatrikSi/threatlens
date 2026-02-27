import uuid

from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.services.tag_feedback import load_feedback_adjustments, record_feedback_events


def test_feedback_adjustments_reflect_positive_and_negative_signals(db_session):
    feed = Feed(name="Feedback Feed", url="https://example.com/feedback.xml", enabled=True, fetch_interval_seconds=1800)
    db_session.add(feed)
    db_session.flush()

    item = Item(
        id=uuid.uuid4(),
        feed_id=feed.id,
        source_guid="feedback-item",
        url="https://example.com/feedback-item",
        canonical_url="https://example.com/feedback-item",
        title="Feedback item",
        summary="summary",
        published_at=None,
        dedupe_key="feedback-item",
        content_hash="a" * 64,
        status="new",
    )
    db_session.add(item)
    db_session.flush()

    user = User(
        id=uuid.uuid4(),
        email="feedback-user@example.com",
        password_hash="hash",
        role="analyst",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    record_feedback_events(
        db_session,
        user_id=user.id,
        item_id=item.id,
        signal_type="manual_add",
        tag_names=["vendor:microsoft", "vendor:microsoft"],
    )
    record_feedback_events(
        db_session,
        user_id=user.id,
        item_id=item.id,
        signal_type="star",
        tag_names=["vendor:microsoft"],
    )
    record_feedback_events(
        db_session,
        user_id=user.id,
        item_id=item.id,
        signal_type="manual_remove",
        tag_names=["campaign:lockbit"],
    )
    db_session.commit()

    adjustments = load_feedback_adjustments(
        db_session,
        tag_names=["vendor:microsoft", "campaign:lockbit"],
    )
    assert adjustments["vendor:microsoft"] > 0
    assert adjustments["campaign:lockbit"] < 0
