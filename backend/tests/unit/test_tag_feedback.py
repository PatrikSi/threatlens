import uuid

import pytest

from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
from app.services.data_access_policy import DataAccessContext, DataPolicyMode
from app.services.tag_feedback import load_feedback_adjustments, record_feedback_events


def test_feedback_adjustments_reflect_positive_and_negative_signals(db_session):
    feed = Feed(
        name="Feedback Feed",
        url="https://example.com/feedback.xml",
        enabled=True,
        fetch_interval_seconds=1800,
    )
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


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("enforced", pytest.approx(1.0 / 12.0)),
        ("audit", pytest.approx(2.0 / 12.0)),
        ("disabled", pytest.approx(2.0 / 12.0)),
    ],
)
def test_feedback_adjustments_filter_at_the_source_only_when_enforced(
    db_session,
    mode: DataPolicyMode,
    expected: float,
):
    visible_feed = Feed(
        name=f"Visible feedback {mode}",
        url=f"https://example.com/feedback-visible-{mode}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    restricted_feed = Feed(
        name=f"Restricted feedback {mode}",
        url=f"https://example.com/feedback-restricted-{mode}.xml",
        enabled=True,
        fetch_interval_seconds=1800,
        handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    db_session.add_all([visible_feed, restricted_feed])
    db_session.flush()
    visible_item = Item(
        id=uuid.uuid4(),
        feed_id=visible_feed.id,
        source_guid=f"feedback-visible-{mode}",
        url=f"https://example.com/feedback-visible-{mode}",
        canonical_url=f"https://example.com/feedback-visible-{mode}",
        title="Visible feedback item",
        summary="summary",
        dedupe_key=f"feedback-visible-{mode}",
        content_hash="b" * 64,
        status="new",
    )
    restricted_item = Item(
        id=uuid.uuid4(),
        feed_id=restricted_feed.id,
        source_guid=f"feedback-restricted-{mode}",
        url=f"https://example.com/feedback-restricted-{mode}",
        canonical_url=f"https://example.com/feedback-restricted-{mode}",
        title="Restricted feedback item",
        summary="summary",
        dedupe_key=f"feedback-restricted-{mode}",
        content_hash="c" * 64,
        status="new",
    )
    user = User(
        id=uuid.uuid4(),
        email=f"feedback-policy-{mode}@example.com",
        password_hash="hash",
        role="analyst",
        is_active=True,
    )
    db_session.add_all([visible_item, restricted_item, user])
    db_session.flush()
    for item in (visible_item, restricted_item):
        record_feedback_events(
            db_session,
            user_id=user.id,
            item_id=item.id,
            signal_type="manual_add",
            tag_names=["policy-feedback"],
        )
    db_session.commit()

    adjustments = load_feedback_adjustments(
        db_session,
        tag_names=["policy-feedback"],
        data_access=DataAccessContext(
            mode=mode,
            policy_revision=1,
            coverage_version=1,
            principal_type="user",
            principal_id=user.id,
            principal_eligible=True,
            allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
        ),
    )

    assert adjustments["policy-feedback"] == expected
