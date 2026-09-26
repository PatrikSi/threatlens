"""Retained indicator provenance keeps its handling labels available."""

import uuid

import pytest

from app.models.data_policy import (
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.iam import IAMGroup
from app.models.intel_assessment import IndicatorAssessment, ItemIntelState
from app.models.ioc import IOC
from app.models.item import Item
from app.models.team import Team
from app.schemas.data_policy import (
    HandlingLabelCreateRequest,
    HandlingLabelStatusRequest,
)
from app.services.data_access_policy import (
    DataPolicyConflict,
    create_handling_label,
    set_handling_label_status,
)
from app.services.data_policy_preflight import full_data_policy_preflight


def _retained_reference(db, *, label_id, assessment):
    feed = Feed(
        name="Reassigned feed", handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID
    )
    feed.url = f"https://example.net/feed/{uuid.uuid4()}"
    db.add(feed)
    db.flush()
    item = Item(
        feed_id=feed.id,
        title="Retained indicator provenance",
        url="https://example.net/article",
        dedupe_key=uuid.uuid4().hex,
        content_hash="a" * 64,
    )
    db.add(item)
    db.flush()
    if not assessment:
        row = ItemIntelState(item_id=item.id, handling_label_id=label_id)
    else:
        group = IAMGroup(
            key="indicator-reviewers", name="Indicator reviewers", source="local"
        )
        indicator = IOC(type="domain", value_raw="evil.net", value_norm="evil.net")
        db.add_all([group, indicator])
        db.flush()
        team = Team(key="indicator-review", name="Review", membership_group_id=group.id)
        db.add(team)
        db.flush()
        row = IndicatorAssessment(
            team_id=team.id,
            item_id=item.id,
            ioc_id=indicator.id,
            handling_label_id=label_id,
            source_revision=1,
            extraction_revision=1,
            verdict="malicious",
            reason="Retained analyst review after the feed moved labels.",
        )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("assessment", [False, True], ids=["inventory", "assessment"])
def test_retained_indicator_label_cannot_be_archived(
    db_session, seed_users, assessment
):
    state = db_session.get(DataPolicyState, 1)
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=state.revision,
            key="retained-intelligence",
            name="Retained intelligence",
        ),
        actor_user_id=seed_users["admin"].id,
    )
    row = _retained_reference(
        db_session, label_id=created.label.id, assessment=assessment
    )
    command = HandlingLabelStatusRequest(
        expected_revision=created.label.revision, active=False
    )
    with pytest.raises(
        DataPolicyConflict, match="retained by derived intelligence"
    ) as error:
        set_handling_label_status(
            db_session,
            label_id=created.label.id,
            payload=command,
            actor_user_id=seed_users["admin"].id,
        )
    assert error.value.context["derived_reference_count"] == 1
    assert db_session.get(HandlingLabel, created.label.id).is_active

    # Removing the actual retained parent releases the label without needing
    # an unrelated event/audit record to hold the policy reference for it.
    db_session.delete(row)
    db_session.flush()
    result = set_handling_label_status(
        db_session,
        label_id=created.label.id,
        payload=command,
        actor_user_id=seed_users["admin"].id,
    )
    assert result.label.is_active is False


@pytest.mark.parametrize("assessment", [False, True], ids=["inventory", "assessment"])
def test_preflight_reports_archived_retained_indicator_labels(
    db_session, seed_users, assessment
):
    state = db_session.get(DataPolicyState, 1)
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=state.revision,
            key="imported-intelligence",
            name="Imported intelligence",
        ),
        actor_user_id=seed_users["admin"].id,
    )
    _retained_reference(db_session, label_id=created.label.id, assessment=assessment)
    db_session.get(HandlingLabel, created.label.id).is_active = False
    db_session.flush()
    result = full_data_policy_preflight(db_session, state=state)
    assert result.blocker_counts["inactive_normalized_label_references"] == 1
    assert not result.ready_for_enforcement
